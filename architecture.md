# OMERO ⇄ Gater Integration — Architecture & Workflows

Deep reference for the OMERO integration on branch `feature/omero_gater_deployment`.
Anchors the deployment plan (`DEPLOYMENT_PLAN.md`) and the per-commit history
(`OMERO_INTEGRATION_REPORT.md` §11). All code references are `file:function`.

---

## 1. Core idea — two operating modes

Gater natively renders **local** mcmicro data (a pyramidal channel OME-TIFF, a
segmentation mask, and a quantification CSV, all on disk). The integration adds a
**second mode** in which the channel image is **never downloaded** — it is streamed
tile-by-tile from OMERO — while the CSV and segmentation are still materialized
locally.

A single config flag switches modes, evaluated per datasource and per tile request:

```
config[datasource]['omero_image_id'] set  AND  'channelFile' == ''   →  OMERO live-tile mode
otherwise                                                            →  local-file mode
```

Everything else in Gater (cell picking, gating, GMMs, histograms) is **mode-agnostic** —
it runs off the in-memory CSV + ball tree, identical in both modes. The integration is
surgically contained to **(A) image import, (B) channel-tile serving, (C) OMERO-side
state storage.**

---

## 2. Layered structure

```
minerva_analysis/
├── __init__.py                 Flask setup: data_path, config.json, db.sqlite3,
│                               BASE_PATH PrefixMiddleware, {{base_path}} context processor
├── server/
│   ├── routes/
│   │   ├── page_routes.py      "/", "/<datasource>" (viewer), "/upload_page"
│   │   ├── import_routes.py    upload + OMERO handoff + save_config + progress SSE
│   │   └── data_routes.py      render/query endpoints incl. the tile route
│   ├── models/
│   │   ├── data_model.py       ← heart: OMERO conn layer, live-tile, state storage,
│   │   │                         load lifecycle, datasource/seg/ball-tree logic
│   │   └── database_model.py    SQLite tables (channelList, gatinglist)
│   └── utils/
│       ├── qptiff_to_ometiff.py       QPTIFF → tiled BigTIFF (disk memmap)
│       ├── omero_csv_to_mcmicro.py    OMERO quant CSV → mcmicro CSV (chunked)
│       └── omero_zarr_to_ometiff.py   OME-NGFF label zarr → pyramidal OME-TIFF
└── client/                     OSD/WebGL viewer; channelList, csvGatingList,
                                channelMatch, dataLayer (talk to the routes above)
```

`data_model.py` holds ~90% of the integration.

---

## 3. OMERO connection layer (concurrency model)

```
_omero_conn          one process-wide BlitzGateway (lazy)
_omero_lock          threading.Lock serializing ALL OMERO access
_omero_pixels_cache  image_id → {pid, dtype, levels}   (pyramid metadata)
_omero_rps_cache     image_id → reusable RawPixelsStore (~35ms/tile saved)
_omero_last_used     idle-timeout clock (_OMERO_IDLE_TIMEOUT = 45s)
_tile_cache          LRU of finished per-(image,channel,level,tile) numpy arrays
```

- **BlitzGateway/Ice is not thread-safe**, so `_ensure_omero_connection()` and every
  `getTile`/annotation call run **under `_omero_lock`** — OMERO access is fully serialized.
- **No liveness probing**: after 45 s idle the connection is dropped and reconnected fresh
  on next use (probing a dead Ice connection can hang with no timeout — a real stall bug).
- **Two cache tiers**: the tile LRU (own lock → concurrent hits, never touches OMERO) and
  the pixel/RPS caches (dropped on reconnect).
- **Credentials are env-based**: `OMERO_HOST/PORT/USER/PASSWORD` (default `root/omero`).
  This is the current auth model — process-wide, single identity → the reason per-user pods
  + `joinSession` are needed for real multi-user.

---

## 4. Data model & globals (statefulness)

The app holds **exactly one datasource in memory at a time**, as module-level globals set
by `load_datasource()`:

```
source        name of the currently-loaded datasource (the "which is loaded" guard)
datasource    the whole quantification CSV as a pandas DataFrame  (heavy)
ball_tree     sklearn BallTree over (X,Y) centroids               (pickle or rebuilt)
seg           segmentation mask: lazy zarr over the local OME-TIFF (label raster)
zarray        COARSEST channel pyramid level, all channels (histogram/slider source)
channels      full channel pyramid — None in OMERO mode (tiles fetched on demand)
config        the parsed config.json (all datasources' entries)
```

`load_datasource(name, reload)`:
1. Guard: if `source == name` and not reloading → no-op.
2. `pd.read_csv` the whole quant CSV → `datasource`.
3. Open the segmentation (`seg`).
4. Channel branch: OMERO mode → read only coarsest OMERO level into `zarray`, `channels=None`;
   else open the local channel file.
5. Set `source` last; rebuild the ball tree if reloading.

State is entirely in-process and non-persistent; switching datasources reloads everything;
there is no per-request user isolation — hence **one pod per user, one datasource each**.

---

## 5. Workflow A — Import via OMERO "Open in Gater"

Turn an OMERO image + its quant/seg attachments into a loadable datasource, without
downloading the image.

### A.0 — Handoff & progress page
`GET /open_from_omero?name&image_id&quant&seg` (`import_routes.py:open_from_omero`) is thin:
validates params (400 if missing), resets progress globals `total_tasks,completed_task,
current_task = 6,0,"Starting"`, renders `omero_loading.html`. That page opens an EventSource
on `/progress` (the bar) and `fetch`es `/open_from_omero_run`, then `document.write`s whatever
the worker returns (wizard or restore-redirect).

> **Deploy note:** `/progress` yields ONE SSE event per connection and returns — the browser
> EventSource reconnects (effectively polling). Behind an ingress, **disable proxy buffering**
> for `text/event-stream`. Progress state is **module-global** → per-user-pod-safe only.

### A.1 — Worker `/open_from_omero_run` (six tracked tasks)
```
task 1  Downloading quantification
   download_omero_annotation(quant_ann, <name>_quantification.csv)   # streams to disk
   if is_omero_csv(header): omero_csv_to_mcmicro(csv)                 # convert IN-PLACE
task 3  Downloading segmentation
   download_omero_annotation(seg_ann, segmentation.zip) → unzip <uuid>.zarr
   omero_zarr_to_ometiff(<uuid>.zarr → <uuid>.ome.tif)               # SLOW (minutes)
── restore check ──
   get_omero_json(image_id, 'config', name)
      if present → _restore_config_entry → write config.json → load → RESTORE-REDIRECT (done)
task 4  Reading channel metadata
   get_omero_channel_info(image_id)   # dims/levels/names from OMERO, NO pixels
task 5-6 assemble config_data + prior_configs → render channel_match.html
```

Sub-mechanisms:
- **`download_omero_annotation`** (`data_model.py`): under `_omero_lock`, `getObject("FileAnnotation")`
  → `getFileInChunks()` → disk. Retries once on Ice drop (large files outlast idle sessions).
  The only place large OMERO file bytes touch pod disk.
- **`omero_csv_to_mcmicro`** (`utils/`): detects OMERO CSVs (`header[0]=='object'` + a
  `*_Mean_intensity` col); converts **streaming in 100k-row chunks** (a naive read of a
  multi-GB quant CSV OOM-kills the container); keeps per-channel Mean + 7 morphology cols;
  renames `object→CellID`, `Centroid_x→X_centroid`; `.tmp` then `os.replace` (in-place-safe).
- **`omero_zarr_to_ometiff`** (`utils/`): navigates OME-NGFF `0/labels/<name>/`, reads the
  multiscales `.zattrs` for ordered levels, writes a **SubIFD pyramidal OME-TIFF** streaming
  ~1 MB tiles/level, **reusing OMERO's pre-computed pyramid** (no recompute). Still the
  **dominant import latency (minutes)**, CPU+disk heavy; dtype stays `uint32` (labels).

**Restore short-circuit:** if `get_omero_json(image_id,'config',name)` finds a saved config for
this exact `(image,name)`, `_restore_config_entry` re-points its env paths at the just-downloaded
files, writes `config.json`, `load_datasource(reload=True)`, returns `omero_restore_redirect.html`
→ viewer, skipping the wizard. (It still paid the download+pyramid cost — restore saves the
*wizard*, not the *I/O*.)

### A.2 — Wizard & `/save_config`
Not restoring → build `config_data` (channel↔CSV mapping candidates, `maxLevel=max(channel,seg)`,
geometry, `channelFile:''`, `omero_image_id`, `prior_configs=list_omero_config_names`) → render
`channel_match.html`. User confirms mapping/transform/"Start from" → `POST /save_config`:
1. Optional log-transform (`logTransform`).
2. Assemble `configData[name]` (featureData, segmentation, `channelFile:''`, omero_image_id,
   imageData `src="/generated/data/<name>/<ch>/"`, geometry) → write `config.json`.
3. `load_datasource(name, reload=True)`.
4. `put_omero_json(image_id,'config',name,entry)` — push config to OMERO.
5. If `baseConfig` chosen → `inherit_render_state(image_id, baseConfig, name)`.

**Deploy implications (A):** writable per-pod data dir; CSV+seg materialized to disk (FLAG 5);
seg-pyramid build is the latency/CPU hotspot; SSE needs proxy-buffering off; env-creds OMERO auth
today (→ per-user `joinSession`); progress globals process-scoped.

---

## 6. Workflow B — Live-tile rendering (hot path)

### B.1 — Tile route
OSD tile sources are `config.imageData[].src = "/generated/data/<name>/<ch>/"`; OSD appends
`/<level>/<x>_<y>.png`. `GET /generated/data/<datasource>/<channel>/<level>/<tile>`
(`data_routes.py`) → `generate_zarr_png(...)` → PIL → `send_file` PNG (`compress_level=0`).

### B.2 — `generate_zarr_png` (`data_model.py:generate_zarr_png`) — the fork
`ix=tx·tileWidth, iy=ty·tileHeight`. Seg vs channel by regex `.*_(\d*)$` — numeric suffix
(`channel_3`) = channel; non-numeric (seg layer) raises `AttributeError` → `segmentation=True`.

**Seg branch (local, always):**
```python
level = min(level, len(seg)-1)                    # clamp to seg pyramid depth
tile  = seg[level][iy:iy+th, ix:ix+tw]            # local zarr slice, label raster (uint32)
tile  = tile.view('uint8')[..., [0,1,2]]          # label ID → R,G,B bytes  (A=0)
```
Label ID carried **losslessly** in RGB (why `compress_level=0`). Seg tile from the downloaded
OME-TIFF — fast, but needs the local file.

**Channel branch:**
```python
if config[..]['omero_image_id']:                  # ← OMERO LIVE PATH
    tile = _get_omero_tile(image_id, channel_num, ix, iy, tileWidth, tileHeight, level)
else:                                             # local zarr (array/pyramid, S-axis aware)
    tile = channels[...][...]
if tile.dtype == np.uint8:                         # ViaWebGL needs u16/u32 textures
    tile = tile.astype(np.uint16) * 257            # 0-255 → 0-65535
```
Channel tile returned as **raw 16-bit intensity** — NOT colorized. The **client WebGL shader
applies color + range** (why color/range are client state, and why render-state annotations
matter). Cleanest boundary: server = intensities, client = appearance.

### B.3 — `_get_omero_tile` internals
```
key=(image,channel,level,x,y,w,h) → _tile_cache (own lock) → hit? return
with _omero_lock:
   cache=_omero_pixel_cache(image)                # {pid,dtype,levels}  levels: full→small
   target_ds = 2**level                           # client uniform downsample
   ds[j] = full_w / levels[j][0]                  # OMERO sparse factors, e.g. [1,4,16,32]
   j = finest level with ds[j] <= target_ds; extra = round(target_ds/ds[j])
   map tile → level-j coords (xj,yj,wj,hj), clamp
   rps=_get_rps(image); rps.setResolutionLevel((n-1)-j)   # ⚠ REVERSED index
   for extra-aligned sub-blocks ≤ _OMERO_MAX_RAW_TILE:    # Fix-(a) bound
       buf=rps.getTile(...); sub=block_reduce(buf,extra,mean); place into out
_tile_cache_put(key,out); return out
```
Invariants: **peak mem ≈ step²/tile** regardless of zoom (OOM fix); output **bit-identical** to a
single read+block_reduce. The `setResolutionLevel` reversal is the classic gotcha (OMERO numbers
levels small→large; `levels` large→small).

### B.4 — Cell picking (interaction, seg-independent)
`GET /get_nearest_cell` → `query_for_closest_cell` → `ball_tree.query(k=1)` → `datasource.iloc[idx]`.
No OMERO, no seg raster — pure in-memory centroid lookup. "What's on screen" (tiles) and "which
cell is here" (ball tree) are fully decoupled.

**Deploy implications (B):** every uncached channel tile = a **serialized** OMERO round-trip
(`_omero_lock`); tile LRU absorbs pan-back/toggle; per-tile mem bounded but pod `mem_limit` is the
backstop; **seg tiles are local** (fast, need the file — FLAG 5); a >45 s pause forces a reconnect
on the next tile. Single-user pods make the serialization acceptable; would not multiplex.

---

## 7. Workflow C — Render/config state save & restore

Make OMERO the source of truth for a datasource's appearance + config, round-tripping across hosts.

### C.0 — Storage model
JSON FileAnnotations on the **image**, identified by `(image, kind, dataset_name)`:
```
namespaces:  gater.vida.nyu/config
             gater.vida.nyu/render-state/channel_list
             gater.vida.nyu/render-state/gating_list
within a namespace, identity = annotation DESCRIPTION == dataset_name
file = gater_<kind>__<safe_name>.json ; body = strict JSON (_jsonable: numpy→native, NaN→null)
```

### C.1 — Write (`put_omero_json`, best-effort)
```
with _omero_lock (retry-once on drop):
   img = getObject(Image, image_id)
   old = annotations under ns whose description ∈ ('', name)   # this name + legacy nameless
   createFileAnnfromLocalFile(tmp, ns=ns, desc=name); img.linkAnnotation(new)
   deleteObjects(old)                                          # CREATE-before-DELETE
returns True/False, never raises
```
Create-before-delete → a failed delete never loses data (readers take newest id). Sweeping legacy
nameless annotations migrates the pre-`(image,name)` scheme on first save.

Callers (all **dual-write**: local SQLite + OMERO):
- `/save_channel_list` → `save_channel_list` → records `[channel,start,end,r,g,b,opacity,channel_active]`
  → SQLite `ChannelList` + `put_omero_json(...,'channel_list',name,...)`.
- `/save_gating_list` → `save_gating_list` → `[channel,gate_start,gate_end,gate_active]` + `Lasso`
  rows → SQLite `GatingList` + `put_omero_json(...,'gating_list',...)`.
- `/save_config` → pushes the config entry (A.2).

### C.2 — Read (OMERO-primary, `get_saved_*`)
```
image_id = _omero_image_id_for(name)
if image_id:
   remote = get_omero_json(image_id, kind, name)     # newest ann with description==name
   if remote is not None: return remote               # OMERO wins
local = pickle.loads(SQLite row)                       # fallback
if image_id: put_omero_json(..., local)                # SEED-on-load (migrate local→OMERO)
return local
```
Fresh pod with empty SQLite still restores from OMERO; legacy SQLite-only datasource seeds OMERO
on first open.

### C.3 — Discovery & inherit
- `list_omero_config_names(image_id)` — descriptions under the config ns → names for the OMERO.web
  "Open" selector and the wizard "Start from" dropdown. Cheap (metadata only).
- `inherit_render_state(image_id, src, dst)` — copies `channel_list`+`gating_list` src→dst as an
  **independent** copy (SQLite + OMERO). Powers "Start from a previous config."

### C.4 — Client auto-restore
On viewer init (`main.js`, after all event bindings): `applyChannels('db')` then `applyGates('db')`
→ `GET /get_saved_*` → apply ranges/colors/gates to sliders + renderer, so a restored datasource
returns as-saved without a manual "Load from OMERO."

> **KNOWN-OPEN (see DEPLOYMENT_PLAN.md §11.3):** channel COLOR not landing in the renderer on
> restore; the gating "Load from OMERO" button making the segmentation mask vanish (WebGL desync).

**Deploy implications (C):** writes serialized + resilient (SQLite fallback on OMERO hiccup); SQLite
is per-pod local (redundant fallback), OMERO the portable truth; annotations owned by env identity
(root) today → per-user ownership + discovery scoping needs `joinSession`; read path adds two OMERO
round-trips per datasource open (part of the slow-switch cost, FLAG 3).

---

## 8. Config model (`config.json`)

One JSON object keyed by datasource name; each entry is the render/config contract:
```json
"<name>": {
  "featureData": [{ "src": ".../quant.csv", "xCoordinate": "X_centroid",
                    "yCoordinate": "Y_centroid", "idField": "CellID", "isTransformed": false }],
  "segmentation": ".../<uuid>.ome.tif",
  "channelFile": "",              // empty ⇒ OMERO mode
  "omero_image_id": 56,           // the mode switch + the annotation key
  "height","width","maxLevel","num_channels","tileWidth","tileHeight",
  "imageData": [ {label/Area}, {channels...} ]   // src = "/generated/data/<name>/<ch>/"
}
```
`maxLevel = max(channel_omero_levels, seg_levels)` coordinates the client's pyramid depth across
the (OMERO) channel and (local) segmentation so they stay aligned at every zoom. Paths are
container-absolute (`/app/...`) — the portability issue behind FLAG 4.

---

## 9. Deployment-relevant properties (summary)

| Property | Consequence for deployment |
|---|---|
| One datasource in memory; module-global state | 1 pod = 1 user = 1 loaded datasource; switching reloads; nothing shared/persistent |
| No app-level auth | Isolation from the per-user pod boundary + OMERO's own permissions |
| `_omero_lock` serializes all OMERO I/O | Bounded render concurrency; fine single-user, wouldn't multiplex |
| Env-based OMERO creds (root) | Must become per-user `joinSession` (session UUID handed in) |
| Connection ephemeral, reconnects on idle | Survives OMERO `timeToIdle`? — an IT confirmation item |
| Quant CSV + seg written to pod disk; channel streamed | Memory: whole CSV+ball tree+coarse zarray resident (size ≥4Gi, verify); disk-persist blocks stateless pods (FLAG 5) |
| Tile reads bounded (Fix a) | Per-tile mem ~step²; pod `mem_limit` is the backstop |
| Base-path aware (GATER_BASE_PATH, PrefixMiddleware) | Serve under `/user/<name>/` behind the spawner's per-user ingress |

**Recurring threads shaping deployment:** (1) `_omero_lock` serializes OMERO → single-user pod;
(2) disk materialization of CSV+seg → FLAG 5; (3) env-identity auth → `joinSession`; (4) module-global,
single-datasource, non-persistent state → one pod per user.

Open items and NYU-IT/user confirmation questions live in `DEPLOYMENT_PLAN.md` §7 and §11.3.
