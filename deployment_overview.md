# Gater ⇄ OMERO — Integration & Deployment Overview

Single reference for the OMERO integration on branch `feature/omero_gater_deployment`: what was
built, how it runs, how it is meant to be deployed, and what is still open.

**Consolidates and supersedes** the previous `OMERO_INTEGRATION_REPORT.md` (engineering log),
`architecture.md` (runtime reference) and `DEPLOYMENT_PLAN.md` (deployment plan) in the documentations directory.

**Companions:** `README.md` — what the app is and how to run it. `security_review.md` — the
security/privacy briefing for NYU Langone Information Security.

**Last verified against code:** 2026-07-30, commit `709c37faa` (tag `v1.36_gater`) plus
uncommitted working-tree changes.

---

## Contents

- [1. Status at a glance](#1-status-at-a-glance)
- [2. What the integration does](#2-what-the-integration-does)
- [3. Runtime architecture](#3-runtime-architecture)
- [4. Workflow A — import from OMERO](#4-workflow-a--import-from-omero)
- [5. Workflow B — live-tile rendering](#5-workflow-b--live-tile-rendering)
- [6. Workflow C — state save & restore](#6-workflow-c--state-save--restore)
- [7. Configuration model](#7-configuration-model)
- [8. Deployment design](#8-deployment-design)
- [9. Implementation status](#9-implementation-status)
- [10. Open flags and known issues](#10-open-flags-and-known-issues)
- [11. Open questions for NYU](#11-open-questions-for-nyu)
- [12. Repository and release strategy](#12-repository-and-release-strategy)
- [Appendix A — commit history](#appendix-a--commit-history)
- [Appendix B — reference tables](#appendix-b--reference-tables)

---

## 1. Status at a glance

**Goal.** Let NYU Cancer Center researchers open their OMERO images directly in Gater — a
cell-level visualization and gating tool — from inside their existing OMERO.web workflow, with a
private per-user instance.

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Data-format integration: teach Gater to consume OMERO data and stream image tiles live from OMERO | **Complete**, committed through `v1.36_gater` |
| **Phase 2** | Deployment: "Open in Gater" button in OMERO.web → per-user Gater pod on Kubernetes | **Partially built** — scaffolds exist, per-user identity and authorization are not implemented |

**Deployment state:** runs today only on a developer machine against a local disposable OMERO
stack. Nothing is deployed at NYU; no NYU data has been processed.

**The two things blocking a pilot:**
1. **Per-user identity (`joinSession`) is not implemented.** The application still connects to
   OMERO with static environment credentials (defaulting to `root`). See §9.
2. **No authorization anywhere.** Neither the Gater pod nor the spawner authenticates callers.
   See `security_review.md` SR-03, SR-05, SR-06.

---

## 2. What the integration does

### 2.1 The original problem

Gater natively reads **mcmicro** pipeline output: a pyramidal channel OME-TIFF, a segmentation
mask, and a quantification CSV, all on local disk. OMERO produces none of those shapes:

| Data type | Gater/mcmicro expects | OMERO actually has |
|---|---|---|
| Image | OME-TIFF (CYX, uint16) | QPTIFF (CYX, uint16, often > 4 GB), or RGB crops |
| Segmentation | Pyramidal OME-TIFF | OME-NGFF zarr (5D TCZYX) |
| Quantification | 1 column per channel (`CellID`, `DAPI`, …) | 8 statistics per channel (`object`, `DAPI_Mean_intensity`, …) |
| Column naming | `CellID`, `X_centroid`, `Y_centroid` | `object`, `Centroid_x`, `Centroid_y` |
| Morphology | `MajorAxisLength`, `MinorAxisLength` | `Major_axis`, `Minor_axis` |

OMERO also serves interleaved RGB crops (YXS axes, uint8), which the tile server and WebGL shader
— both of which assumed CYX uint16 — could not render.

### 2.2 Two operating modes

The integration adds a **second mode** in which the channel image is never downloaded. One flag,
evaluated per datasource and per tile request, switches between them:

```
config[datasource]['omero_image_id'] set  AND  'channelFile' == ''   →  OMERO live-tile mode (Needs deployment)
otherwise                                                            →  local-file mode
```

Everything else in Gater — cell picking, gating, GMMs, histograms — is **mode-agnostic**: it runs
off the in-memory quantification table and spatial index, identically in both modes. The
integration is surgically contained to **(A) import, (B) channel-tile serving, (C) OMERO-side
state storage**.

In OMERO mode the **image is streamed and never copied**; the quantification table and
segmentation mask are still materialized on local disk; streaming needs to be tested (see FLAG 5, §10).

### 2.3 The three converters

All in `minerva_analysis/server/utils/`.

**`qptiff_to_ometiff.py`** — QPTIFF (Akoya/Vectra scanner output) → tiled BigTIFF.
- **BigTIFF output**: test files are 16.69 GB (8-channel) and 42.68 GB (28-channel), far past the
  4 GB standard-TIFF limit.
- **Disk-backed memmap**: channels are read one at a time into a temporary `numpy.memmap` on
  disk, which is handed to `tifffile.TiffWriter` to read tiles from on demand. Peak RAM drops
  from ~17 GB to ~2 GB.
- **Tiled output** `(1024, 1024)` for efficient viewer access; **CYX series grouping** via
  `metadata={'axes': 'CYX'}` so all pages form one series.
- Handles CYX, IYX and YXS input layouts. Channel names come from the filename downstream, not
  from OME metadata.

**`omero_csv_to_mcmicro.py`** — OMERO quantification CSV → mcmicro format.
- **Auto-detection** (`is_omero_csv`): first column is `object` **and** at least one column ends
  in `_Mean_intensity` — so mcmicro CSVs (starting with `CellID`) pass through untouched.
- **Mean-only extraction**: of the 8 statistics per channel (Mean, Max, Min, P25, Median, P75,
  P95, Std_dev), only Mean is kept, matching mcmicro's one-value-per-channel convention.
- **Streaming, 100k-row chunks** — a naive whole-file read of a multi-GB quantification table
  exhausts container memory.
- **In-place** via `.tmp` + `os.replace`, so everything downstream sees mcmicro format.
- Renames: `object→CellID`, `Centroid_x→X_centroid`, `Centroid_y→Y_centroid`,
  `<Ch>_Mean_intensity→<Ch>`, `Major_axis→MajorAxisLength`, `Minor_axis→MinorAxisLength`.
  Suffix stripping is exact, so multi-word markers survive (`Pan_Cytokeratin_Mean_intensity` →
  `Pan_Cytokeratin`).

**`omero_zarr_to_ometiff.py`** — OME-NGFF label zarr → pyramidal OME-TIFF.
- Navigates `0/labels/<name>/`, reads the multiscales `.zattrs` for ordered levels, and **reuses
  OMERO's pre-computed pyramid** rather than recomputing downsamples.
- Writes a **SubIFD pyramidal OME-TIFF**, streaming ~1 MB tiles per level; zlib-compressed;
  dtype stays `uint32` (label IDs).
- Extracts the YX plane from 5D TCZYX by indexing `[0, 0, 0, :, :]`.
- **This is the dominant import latency** — minutes on a large slide, CPU and disk heavy.

### 2.4 The rendering fix that mattered most

Pixel data reaches the browser through a hand-rolled binary path:

```
tile (numpy) → PIL PNG (compress_level=0) → browser → ViaWebGL RG8UI texture
  → fragment shader:  value = (R*255 + G) / 65535
    → range_clamp to [sliderMin/65536, sliderMax/65536] → displayed
```

`imageBitRange` is hard-coded to `[0, 65536]` in `dataLayer.js`. OMERO RGB crops are **uint8**,
so without scaling their values 0–255 map to 0.000–0.004 in the shader — a black image. The fix
multiplies by **257** (`0→0`, `255→65535`, exact because `65535/255 = 257`), applied in **two
places** so tiles and histogram sliders agree: tile serving (`generate_zarr_png`) and histogram
computation (`load_datasource`).

Segmentation tiles use the same PNG path but carry **label IDs losslessly in the RGB bytes**
(`tile.view('uint8')[..., [0,1,2]]`) — which is why PNG compression is disabled.

---

## 3. Runtime architecture

### 3.1 Layout

```
minerva_analysis/
├── __init__.py                 Flask setup: data_path, config.json, db.sqlite3,
│                               BASE_PATH PrefixMiddleware, {{base_path}} processor
├── server/
│   ├── routes/
│   │   ├── page_routes.py      "/", "/<datasource>" (viewer), "/upload_page"
│   │   ├── import_routes.py    upload + OMERO handoff + save_config + progress SSE
│   │   └── data_routes.py      render/query endpoints incl. the tile route
│   ├── models/
│   │   ├── data_model.py       ← the heart: OMERO connection layer, live tiles,
│   │   │                         state storage, load lifecycle, seg/ball-tree logic
│   │   └── database_model.py   SQLite tables (channelList, gatinglist)
│   └── utils/                  the three converters (§2.3)
└── client/                     OpenSeadragon/WebGL viewer; channelList, csvGatingList,
                                channelMatch, dataLayer
```

`data_model.py` holds roughly 90% of the integration.

### 3.2 OMERO connection layer (concurrency & identity)

```
_omero_conn          one process-wide BlitzGateway (lazy)
_omero_lock          threading.Lock serializing ALL OMERO access
_omero_pixels_cache  image_id → {pid, dtype, levels}   (pyramid metadata)
_omero_rps_cache     image_id → reusable RawPixelsStore (~35 ms/tile saved)
_omero_last_used     idle clock (_OMERO_IDLE_TIMEOUT = 45 s)
_tile_cache          LRU of finished per-(image,channel,level,tile) arrays
                     (GATER_TILE_CACHE_SIZE, default 256)
```

- **BlitzGateway/Ice is not thread-safe**, so `_ensure_omero_connection()` and every
  `getTile`/annotation call run **under `_omero_lock`** — all OMERO access is serialized.
- **No liveness probing.** After 45 s idle the connection is dropped and rebuilt fresh on next
  use. Probing a dead Ice connection can hang with no client-side timeout — a real stall bug we
  hit, which is why this is deliberate.
- **Two cache tiers**: the tile LRU has its own lock (concurrent hits never touch OMERO); the
  pixel/RawPixelsStore caches are cleared on reconnect.
- **Credentials are environment-based** — `OMERO_HOST`, `OMERO_PORT`, `OMERO_USER`,
  `OMERO_PASSWORD` (defaults `root`/`omero`). **This is a single process-wide identity and is the
  reason per-user pods plus `joinSession` are required for real multi-user use.** See §9 and
  `security_review.md` SR-01.

### 3.3 State model — why one pod per user

The application holds **exactly one datasource in memory at a time**, in module-level globals set
by `load_datasource()`:

```
source        name of the currently-loaded datasource (the "which is loaded" guard)
datasource    the whole quantification table as a pandas DataFrame  (heavy)
ball_tree     sklearn BallTree over (X, Y) centroids                (pickled or rebuilt)
seg           segmentation mask: lazy zarr over the local OME-TIFF (label raster)
zarray        COARSEST channel pyramid level, all channels (histogram/slider source)
channels      full channel pyramid — None in OMERO mode (tiles fetched on demand)
config        the parsed config.json (all datasources' entries)
_channel_axes tifffile axes string of the loaded image ('CYX' / 'IYX' / 'YXS')
```

`load_datasource(name, reload)`:
1. If `source == name` and not reloading → no-op.
2. `pd.read_csv` the whole quantification table → `datasource`.
3. Open the segmentation → `seg`.
4. Channel branch: OMERO mode → read only the coarsest OMERO level into `zarray`, leave
   `channels = None`; otherwise open the local channel file.
5. Set `source` last; rebuild the spatial index if reloading.

State is entirely in-process and non-persistent. Switching datasources reloads everything. There
is **no per-request user isolation** — hence **one pod per user, one datasource each**.

---

## 4. Workflow A — import from OMERO

Turn an OMERO image plus its quantification/segmentation attachments into a loadable datasource,
without downloading the image.

### 4.1 Handoff and progress page

`GET /open_from_omero?name&image_id&quant&seg` is thin: validate parameters (400 if missing),
reset the progress globals (`total_tasks, completed_task, current_task = 6, 0, "Starting"`), and
render `omero_loading.html`. That page opens an `EventSource` on `/progress` for the bar and
`fetch`es `/open_from_omero_run`, then writes whatever the worker returns (wizard or restore
redirect).

> **Deploy note.** `/progress` yields **one** SSE event per connection and returns — the browser's
> EventSource reconnects, so it behaves as polling. Behind an ingress, **disable proxy buffering**
> for `text/event-stream`. Progress state is module-global, so it is only safe per user pod.

### 4.2 The worker — six tracked tasks

```
task 1  Downloading quantification
   download_omero_annotation(quant_ann, <name>_quantification.csv)   # streams to disk
   if is_omero_csv(header): omero_csv_to_mcmicro(csv)                # convert IN-PLACE
task 3  Downloading segmentation
   download_omero_annotation(seg_ann, segmentation.zip) → unzip <uuid>.zarr
   omero_zarr_to_ometiff(<uuid>.zarr → <uuid>.ome.tif)               # SLOW (minutes)
── restore check ──
   get_omero_json(image_id, 'config', name)
      if present → _restore_config_entry → write config.json → load → RESTORE REDIRECT (done)
task 4  Reading channel metadata
   get_omero_channel_info(image_id)   # dims/levels/names from OMERO, NO pixels
task 5-6 assemble config_data + prior_configs → render channel_match.html
```

**`download_omero_annotation`** (`data_model.py:280`): under `_omero_lock`, resolve the
`FileAnnotation` and stream it to disk via `getFileInChunks()`, retrying once on an Ice drop
(large files outlive idle sessions). **This is the only place large OMERO file bytes touch pod
disk.**

### 4.3 Restore short-circuit

If `get_omero_json(image_id, 'config', name)` finds a saved config for this exact
`(image, name)`, `_restore_config_entry` re-points its environment-specific paths at the
just-downloaded files, writes `config.json`, reloads the datasource, and returns
`omero_restore_redirect.html` → straight to the viewer, **skipping the channel-match wizard**.

Note it still paid the download and pyramid-build cost: restore saves the *wizard*, not the *I/O*.

### 4.4 Wizard and `/save_config`

If not restoring, build `config_data` (channel↔CSV mapping candidates,
`maxLevel = max(channel, seg)`, geometry, `channelFile: ''`, `omero_image_id`, and
`prior_configs` from `list_omero_config_names`) and render `channel_match.html`. The user
confirms the mapping, the optional log transform, and optionally a "Start from" prior config,
then `POST /save_config`:

1. Optional log transform.
2. Assemble `configData[name]` (featureData, segmentation, `channelFile: ''`, `omero_image_id`,
   `imageData` with `src = "/generated/data/<name>/<ch>/"`, geometry) → write `config.json`.
3. `load_datasource(name, reload=True)`.
4. `put_omero_json(image_id, 'config', name, entry)` — push the config to OMERO.
5. If a `baseConfig` was chosen → `inherit_render_state(image_id, baseConfig, name)`.

**Deployment implications:** needs a writable per-pod data directory; the quantification table and
segmentation are materialized on disk (FLAG 5); the segmentation pyramid build is the latency and
CPU hotspot; SSE needs proxy buffering off; OMERO auth is environment credentials today
(→ per-user `joinSession`); progress state is process-scoped.

---

## 5. Workflow B — live-tile rendering

### 5.1 The tile route

OpenSeadragon tile sources are `config.imageData[].src = "/generated/data/<name>/<ch>/"`; OSD
appends `/<level>/<x>_<y>.png`. `GET /generated/data/<datasource>/<channel>/<level>/<tile>`
(`data_routes.py:324`) → `generate_zarr_png(...)` → PIL → `send_file` PNG with
`compress_level=0`.

### 5.2 `generate_zarr_png` — the fork

`ix = tx·tileWidth`, `iy = ty·tileHeight`. Segmentation vs channel is decided by a regex on the
layer name `.*_(\d*)$`: a numeric suffix (`channel_3`) means channel; a non-numeric name (the
segmentation layer) raises `AttributeError` → `segmentation = True`.

**Segmentation branch (always local):**
```python
level = min(level, len(seg) - 1)                 # clamp to seg pyramid depth
tile  = seg[level][iy:iy+th, ix:ix+tw]           # local zarr slice, uint32 label raster
tile  = tile.view('uint8')[..., [0, 1, 2]]       # label ID → R, G, B bytes (A = 0)
```

**Channel branch:**
```python
if config[..]['omero_image_id']:      # ← OMERO LIVE PATH
    tile = _get_omero_tile(image_id, channel_num, ix, iy, tileWidth, tileHeight, level)
else:                                 # local zarr (array or pyramid, S-axis aware)
    tile = channels[...][...]
if tile.dtype == np.uint8:            # ViaWebGL needs u16/u32 textures
    tile = tile.astype(np.uint16) * 257
```

The channel tile is returned as **raw 16-bit intensity, not colorized** — the client's WebGL
shader applies color and range. That is why color/range are client-side state, and why the
render-state annotations of Workflow C exist. Clean boundary: **server = intensities, client =
appearance.**

### 5.3 `_get_omero_tile` internals

```
key = (image, channel, level, x, y, w, h) → _tile_cache (own lock) → hit? return
with _omero_lock:
   cache = _omero_pixel_cache(image)             # {pid, dtype, levels}  levels: full→small
   target_ds = 2**level                          # the client's uniform downsample
   ds[j] = full_w / levels[j][0]                 # OMERO's SPARSE factors, e.g. [1, 4, 16, 32]
   j = finest level with ds[j] <= target_ds ;  extra = round(target_ds / ds[j])
   map tile → level-j coordinates (xj, yj, wj, hj), clamp
   rps = _get_rps(image); rps.setResolutionLevel((n-1) - j)     # ⚠ REVERSED index
   for extra-aligned sub-blocks of at most _OMERO_MAX_RAW_TILE per side:
       buf = rps.getTile(...); sub = block_reduce(buf, extra, mean); place into out
_tile_cache_put(key, out); return out
```

**Why the mapping exists:** the Gater client and the segmentation OME-TIFF use a *uniform*
power-of-two pyramid (level *k* = full resolution ÷ 2ᵏ), but OMERO's stored pyramid is typically
**sparse and non-power-of-two** (an SVS slide may keep only 1×, ¼, 1/16, 1/32). Mapping the client
level straight onto the OMERO level index mis-scales every level but full resolution and leaves
tiles blank once zoomed.

**Two invariants worth knowing:**
- **Peak memory ≈ step² per tile regardless of zoom.** An earlier version issued one unbounded
  `getTile` whose region could span most of the plane — hundreds of MB to GB, roughly tripled by
  `frombuffer` + `block_reduce` + `astype` — which OOM-killed the container (exit 137) on a
  zoomed tonsil slide. The sub-block loop fixed it. Cap: `GATER_OMERO_MAX_RAW_TILE`, default 2048.
- **Output is bit-identical** to the old single read, because every sub-block starts on an `extra`
  boundary and only the trailing block is edge-padded — exactly as a single `block_reduce` would
  pad.

**The classic gotcha:** `setResolutionLevel()` numbers levels **small→large** while
`getResolutionDescriptions()` returns them **large→small**, hence the `(n-1) - j` inversion.

### 5.4 Cell picking

`GET /get_nearest_cell` → `query_for_closest_cell` → `ball_tree.query(k=1)` →
`datasource.iloc[idx]`. No OMERO, no segmentation raster — a pure in-memory centroid lookup.
"What is on screen" (tiles) and "which cell is here" (spatial index) are fully decoupled.

**Deployment implications:** every uncached channel tile is a **serialized** OMERO round trip; the
tile LRU absorbs pan-back and channel toggles; per-tile memory is bounded but the pod memory limit
is the backstop; **segmentation tiles are local** (fast, but require the file — FLAG 5); a pause
longer than 45 s forces a reconnect on the next tile. Single-user pods make the serialization
acceptable; this would not multiplex.

---

## 6. Workflow C — state save & restore

Make OMERO the source of truth for a datasource's appearance and configuration, so it round-trips
across hosts and a pod can be disposable.

### 6.1 Storage model

JSON FileAnnotations on the **Image**, identified by `(image, kind, dataset_name)`:

```
namespaces:  gater.vida.nyu/config
             gater.vida.nyu/render-state/channel_list
             gater.vida.nyu/render-state/gating_list
within a namespace, identity = annotation DESCRIPTION == dataset_name
file = gater_<kind>__<safe_name>.json ; body = strict JSON
       (_jsonable: numpy → native, NaN → null)
```

The namespace is stable **per kind**, so discovery is one indexed namespace query; the
**description** carries the exact dataset name. A re-save of the same name replaces only its own
annotation, while **different names on the same image coexist** — the same OMERO image can be
analyzed several times under different names, each its own datasource.

### 6.2 Write path (`put_omero_json`, best-effort)

```
with _omero_lock (retry once on drop):
   img = getObject(Image, image_id)
   old = annotations under ns whose description ∈ ('', name)   # this name + legacy nameless
   createFileAnnfromLocalFile(tmp, ns=ns, desc=name); img.linkAnnotation(new)
   deleteObjects(old)                                          # CREATE BEFORE DELETE
returns True/False, never raises
```

Create-before-delete means a failed delete can never lose data (readers take the newest id).
Sweeping legacy nameless annotations migrates the pre-`(image, name)` scheme on first save.

Callers, all **dual-write** (pod-local SQLite **and** OMERO):
- `/save_channel_list` → records `[channel, start, end, r, g, b, opacity, channel_active]`.
- `/save_gating_list` → records `[channel, gate_start, gate_end, gate_active]` plus Lasso rows.
- `/save_config` → pushes the config entry.

### 6.3 Read path (OMERO-primary)

```
image_id = _omero_image_id_for(name)
if image_id:
   remote = get_omero_json(image_id, kind, name)     # newest annotation with description == name
   if remote is not None: return remote               # OMERO wins
local = pickle.loads(SQLite row)                       # fallback
if image_id: put_omero_json(..., local)                # SEED on load (migrate local → OMERO)
return local
```

A fresh pod with an empty SQLite database still restores from OMERO; a legacy SQLite-only
datasource seeds OMERO on first open.

**Config stays local-primary** as the bootstrap loader, because `config.json` is also the index
mapping datasource name → `omero_image_id`; it cannot be read from OMERO without already knowing
the id. The OMERO copy is for backup and portability.

### 6.4 Discovery, inheritance and selection UIs

- **`list_omero_config_names(image_id)`** — descriptions under the config namespace. Cheap
  (metadata only).
- **`inherit_render_state(image_id, src, dst)`** — copies `channel_list` + `gating_list` from one
  name to another as an **independent** copy (both SQLite and OMERO).
- **OMERO.web-side "Open" dropdown** (primary): the Gater tab lists saved configs for the image —
  *New datasource* or a saved name. Picking a saved name locks the Dataset Name field to it, so
  Gater's restore matches `(image, name)`, skips the wizard, and opens straight into the viewer.
- **Gater-side wizard "Start from" dropdown** (secondary fallback): seeds a new datasource's
  render state from a prior config on the same image.

### 6.5 Client auto-restore

On viewer initialization (`main.js`, after **all** event bindings are registered):
`applyChannels('db')` then `applyGates('db')` → `GET /get_saved_*` → apply ranges, colors and
gates to the sliders and renderer, so a restored datasource returns as it was saved without a
manual "Load from OMERO" click. The binding order matters: `applyChannels` fires a colour-transfer
event that must already have a handler.

**Deployment implications:** writes are serialized and resilient (SQLite fallback on an OMERO
hiccup); SQLite is a per-pod redundant fallback while OMERO is the portable truth; annotations are
owned by the environment identity (`root`) today — per-user ownership and owner-scoped discovery
need `joinSession` (FLAG 7); the read path adds two OMERO round trips per datasource open, part of
the slow-switch cost (FLAG 3).

---

## 7. Configuration model

`config.json` is one object keyed by datasource name; each entry is the render/config contract:

```json
"<name>": {
  "featureData": [{ "src": ".../quant.csv", "xCoordinate": "X_centroid",
                    "yCoordinate": "Y_centroid", "idField": "CellID",
                    "isTransformed": false }],
  "segmentation": ".../<uuid>.ome.tif",
  "channelFile": "",              // empty ⇒ OMERO mode
  "omero_image_id": 56,           // the mode switch AND the annotation key
  "height", "width", "maxLevel", "num_channels", "tileWidth", "tileHeight",
  "imageData": [ {label/Area}, {channels...} ]   // src = "/generated/data/<name>/<ch>/"
}
```

`maxLevel = max(channel_omero_levels, seg_levels)` coordinates the client's pyramid depth across
the (OMERO) channel image and the (local) segmentation so they stay aligned at every zoom.

Paths are container-absolute (`/app/...`), which is the portability issue behind FLAG 4.

---

## 8. Deployment design

### 8.1 Why per-user pods

Gater holds its active dataset in module-level globals and has no authentication (§3.3). A
per-user pod — the JupyterHub spawner pattern — gives each researcher an isolated process, so the
global-state limitation stops mattering. It does **not**, by itself, solve authorization: see §9.

### 8.2 Target architecture

```
  Researcher's browser
    │ 1. logged into OMERO.web, clicks "Open in Gater" on an image
    ▼
  OMERO.web (+ omero-gater plugin)
    │ 2. POST {user, omero_session, omero_host, image_id}
    ▼
  SPAWNER  ── 3. create ──▶  Per-user GATER POD (Deployment + Service + Ingress)
    - creates/reuses the user's pod via the K8s API   - env: OMERO_SESSION, OMERO_HOST, base path
    - registers the route                             - connects to OMERO AS THAT USER
    - culls idle pods                                 - 4. streams tiles from OMERO:4064
    │ 6. redirect the user to /user/<user>/
    ▼
  INGRESS  ── 5. route /user/<user>/ ──▶  that user's pod
```

**What actually runs where.** On Kubernetes we deploy **only the spawner**
(Deployment + Service + RBAC, with Ingress/ConfigMap/NetworkPolicy to follow); the spawner
dynamically creates the per-user Gater Deployments/Services/Ingresses. The **plugin installs into
NYU's existing OMERO.web**. **OMERO itself is external** and NYU-operated — the `docker-compose`
OMERO stack in this repository is **local development only**.

### 8.3 Component 1 — the `omero-gater` OMERO.web plugin

A small pip-installable **Django app**, registered through OMERO.web's documented plugin hooks.
OMERO.web is Django and must be extended with a custom app, never by editing core; only
`webgateway` and `api` are stable public APIs, so we use `right_plugins` rather than webclient
template overrides.

```
omero-gater/
├── setup.py / setup.cfg
└── omero_gater/
    ├── settings.py     CUSTOM_SETTINGS_MAPPINGS: spawner_url, base_url, omero_host
    ├── urls.py         /gater/ , /gater/tab/<image_id>/ , /gater/open/<image_id>/
    ├── views.py        index, gater_tab, open_in_gater
    └── templates/gater/…  the right-panel tab and the "Open in Gater" form
```

Registration:
```bash
omero config append omero.web.apps '"omero_gater"'
omero config append omero.web.ui.right_plugins \
  '["Gater", "gater/webclient_plugins/right_plugin.gater.js.html", "gater_tab"]'
omero config set omero.web.gater.spawner_url '"http://gater-hub.gater.svc:8081"'
omero config set omero.web.gater.base_url    '"https://gater.med.nyu.edu"'
omero config set omero.web.gater.omero_host  '"omero.med.nyu.edu"'
omero web restart
```

**What the tab does:** shows a "Gater" tab whenever a single Image is selected; the tab body lets
the researcher name the datasource, pick the quantification CSV and segmentation from the image's
attachments, and — if saved configs exist for that image — choose one from an **Open** dropdown to
restore instead of starting fresh.

**Two modes** in `open_in_gater`:
- **Direct mode** (`spawner_url` empty): redirect straight to
  `<base_url>/open_from_omero?…` — a single shared Gater instance. This is what local development
  uses.
- **Spawner mode**: `POST <spawner_url>/launch`, then redirect to
  `<base_url>/user/<username>/open_from_omero?…`.

Both pass `name`, `image_id`, `quant` and `seg` as query parameters.

### 8.4 Component 2 — the `gater-spawner` service

FastAPI + the Kubernetes Python client.

```
POST /launch  {user, omero_session, omero_host, image_id, omero_port?}
   → ensures Deployment + Service (+ Ingress) named gater-<safe-user>
   → 200 {"status":"ok","user":…,"url":"https://<host>/user/<user>/"}
GET  /healthz     DELETE /stop/{user}     GET /apps
```

Environment injected into each per-user pod:

| env | meaning |
|---|---|
| `OMERO_HOST` / `OMERO_PORT` | how the pod reaches OMERO |
| `OMERO_SESSION` | the session UUID to join — **the app does not read this yet** (§9) |
| `IMAGE_ID` | the image to open — **unused; the redirect URL carries it instead** (§9) |
| `GATER_BASE_PATH` | `/user/<user>` — the prefix the pod must serve under |

**Routing:** each user gets an Ingress rule `host=<PUBLIC_HOST>, path=/user/<user> (Prefix)` → their
Service → their pod, with **no path rewrite** — which is exactly what `GATER_BASE_PATH` exists for.

**RBAC** (`deploy/rbac.yaml`): a namespaced ServiceAccount + Role granting CRUD on
deployments/services/ingresses and read-only on pods. No cluster-scoped permissions.

**Resource limits** are already configured per pod: `GATER_POD_CPU_REQUEST/LIMIT` (250m / 2) and
`GATER_POD_MEM_REQUEST/LIMIT` (512Mi / **4Gi**). The 4Gi limit should be validated against the
tonsil dataset, whose whole quantification table plus spatial index is resident.

**Known simplifications** (documented in the spawner's own README): culling is **launch-age TTL,
not true idle**; every launch patches the Deployment env and therefore triggers a **full pod
rollout**; there is **no PVC**, so per-user working data is ephemeral.

### 8.5 Component 3 — base-path serving

Serving under `/user/<name>/` is the classic proxied-Flask breakage point, and is handled:

- `GATER_BASE_PATH` is normalized in `minerva_analysis/__init__.py` and stored as
  `app.config['BASE_PATH']`.
- `PrefixMiddleware` strips the prefix from `PATH_INFO` into `SCRIPT_NAME`, so routes defined at
  `/` still match.
- A context processor exposes `{{ base_path }}` to every template; `base.html` and
  `omero_loading.html` inject it as `window.GATER_BASE_PATH`.
- Client code (`services/basePath.js`, `imageViewer.js`) prefixes generated URLs, including the
  OpenSeadragon icon path and the GLSL shader URLs.

Empty by default → served at the root, behaviour unchanged.

### 8.6 The intended identity handshake (not yet implemented)

OMERO sessions are shareable by UUID, so the pod can **join** the researcher's session without a
password:

```python
# OMERO.web plugin side — conn is already authenticated as the researcher
ec = conn.getEventContext()
session_uuid = ec.sessionUuid          # ← the linchpin

# Gater pod side — replaces BlitzGateway(user, password, …) in data_model.py
client = omero.client(OMERO_HOST, 4064)
client.joinSession(OMERO_SESSION_UUID)  # from env, passed by the spawner
conn = BlitzGateway(client_obj=client)
conn.c.enableKeepAlive(60)
```

The pod then reads tiles **as the researcher**, so OMERO's own permission model applies and no
password ever leaves OMERO.web. Because the button only appears inside an authenticated OMERO
session, **OMERO login becomes the authentication boundary**, and NYU SSO in front of the hub
becomes optional hardening rather than a prerequisite.

⚠️ Caveats: a joined session inherits the original's `timeToIdle`/`timeToLive`; and the pod
receives the researcher's **full** OMERO privileges for its lifetime, not a capability scoped to
one image. Both need NYU input (§11).

**Pixel-data path decision.** We use direct BlitzGateway `getTile` rather than proxying through
OMERO's public `webgateway` JSON API, because the latter returns **RGB composites**, not the
per-channel raw uint16 tiles Gater's shader requires. The trade-off is that the pod needs
omero-py/Ice and a direct route to OMERO:4064.

### 8.7 Authentication options considered

| Model | How it works | Precondition |
|---|---|---|
| **A. Trust the OMERO session** (chosen) | Button visible only to a logged-in OMERO user; the session UUID is handed to the spawner; the pod joins it | OMERO.web can expose a usable session UUID |
| **B. NYU SSO everywhere (OIDC/SAML)** | Both OMERO and the hub authenticate against the NYU IdP; one login covers both | NYU issues an OIDC client / SAML SP **and** OMERO is wired to the same IdP |
| **C. NYU SSO for the hub + separate OMERO login** | SSO to reach the hub; OMERO credentials handled separately | Requires credential mapping |

Model A is the design of record. Whether NYU requires B as well is an open question (§11).

### 8.8 Storage and data movement

- **Imagery** stays in OMERO, streamed live. No copy.
- **Per-user working data** (the quantification table, the converted segmentation, saved gates and
  config) is written to pod-local disk today, with **no PVC** — so it is destroyed with the pod.
  A PVC would improve usability (avoiding the minutes-long re-conversion) but creates a retention
  question that must be answered first.
- **Writing results back into OMERO** currently covers render/config state only (Workflow C).
  Exporting derived data (e.g. gated populations) back into OMERO as annotations is a separate
  feature to scope.

### 8.9 Suggested phasing

0. **De-risk** — answer the §11 questions; prove one pod connects to OMERO as a specified user and
   renders an image. *(The container build risk — omero-py/zeroc-ice — is now resolved; see §9.)*
1. **Manual multi-user** — deploy the spawner in the namespace; reach a per-user pod by URL, with
   authentication working.
2. **OMERO integration** — the plugin's button → spawner handshake; the pod opens on the chosen
   image.
3. **Hardening** — per-pod authorization guard, idle culling, retention policy, TLS, resource
   limits, audit logging, compliance sign-off.

---

## 9. Implementation status

The deployment plan originally listed five required application changes. Verified status as of
2026-07-30:

| # | Change | Status |
|---|---|---|
| 1 | **Fix the Dockerfile** — add `omero-py` + `zeroc-ice` | ✅ **Done.** `gater/Dockerfile` installs Glencoe Software's prebuilt Ice 3.6.5 wheel (linux/amd64, CPython 3.9) then `omero-py`. This was the top spike risk and it is resolved. Build on Apple Silicon with `--platform=linux/amd64`. |
| 2 | **Per-user OMERO credentials** (`joinSession`) | ❌ **Not implemented.** `data_model.py:101–138` still uses `OMERO_USER`/`OMERO_PASSWORD`, defaulting to `root`/`omero`. The plugin sends the session UUID and the spawner injects `OMERO_SESSION`, but **the application never reads it**. |
| 3 | **Base-path awareness** | ✅ **Done.** `PrefixMiddleware`, `{{ base_path }}`, `window.GATER_BASE_PATH`, and client-side URL prefixing (§8.5). |
| 4 | **Launch-time dataset selection** | ⚠️ **Done differently.** The spawner injects `IMAGE_ID`, which the app ignores; the working mechanism is the plugin redirecting to `/user/<user>/open_from_omero?name&image_id&quant&seg`, which is strictly better (it also carries the dataset name and attachment ids). `IMAGE_ID` should be dropped or documented as unused. |
| 5 | **Per-pod authorization guard** | ❌ **Not implemented.** Nothing rejects a request to another user's pod. |

**Other contract gaps between the three components** (verified, worth fixing together with #2):

- **No readiness handshake.** The plugin `POST`s `/launch` and redirects immediately, while every
  launch patches the Deployment env and forces a full pod rollout — so the first click reliably
  lands on a pod that is not ready.
- **Two independent URL constructions.** The spawner returns a URL that the plugin ignores,
  building its own instead.
- **Name normalization mismatch.** Kubernetes resource names use a sanitized slug plus a hash,
  while the ingress path uses the raw username.
- **`/launch` is unauthenticated** and there is no NetworkPolicy — see `security_review.md`.

---

## 10. Open flags and known issues

| Flag | Issue |
|---|---|
| **FLAG 1** | **Channel colour is not restored** (deprioritized — "OK for now"). Restore correctly brings back channel active state, intensity ranges and gating, but colour stays default (white). Not fully root-caused: `viewerManager.channel_add` reads `colorConnector[idx].color` and `imageViewer.updateChannelColors` only applies to an already-active channel; both expect a `d3.rgb`. Storing the right format and firing the event after activation did not fix it — likely how the WebGL shader consumes the colour, or the event still not landing. |
| **FLAG 2** | **"Load Gating from OMERO" makes the segmentation mask vanish.** Gate *values* stay correct, but the mask overlay disappears — a WebGL render desync in `imageViewer.js` (`bindGatings` / `show_subset`). Needs runtime debugging. |
| **FLAG 3** | **Slow datasource switch.** Two causes: (a) pre-existing — one datasource in memory, so every switch is a from-scratch reload dominated by reading the whole quantification CSV (~100 MB → seconds to tens of seconds), plus the spatial index and a coarse-plane OMERO read; (b) added by Workflow C — the OMERO-primary reads add two annotation round trips (each possibly forcing a reconnect, plus a seeding write). *Levers:* cache `get_omero_json` per `(image, kind)` and keep seeding off the load critical path; an LRU of the last N datasources, or a parquet/feather sidecar instead of `read_csv`. |
| **FLAG 4** | **`config.json` paths are container-absolute.** In deployment they should reference OMERO paths. `_restore_config_entry` is the seam that re-points them. |
| **FLAG 5** | **No-local-persist / live-stream quant + segmentation.** Both are still downloaded and converted to disk, because segmentation tile serving needs a local pyramidal OME-TIFF. **Key experiment:** can OMERO serve the mask as a tileable pyramid like the channel image? It must exist as its own OMERO Image, with matching dimensions, and its pyramid must be **nearest-neighbour** — averaging label IDs corrupts them. `probe_omero_live.py` (in this directory) measures exactly this; it has not yet been run against a real OMERO. |
| **FLAG 6** | **Restore drops phenotype (celltype) and cluster data**, because re-open only re-downloads quantification and segmentation. `_restore_config_entry` drops those keys to avoid load failures. |
| **FLAG 7** | **Annotation ownership is the environment identity (`root`).** For real per-user ownership and discovery, revisit under per-user `joinSession`; the discovery query should filter by owner/group so a user sees only their own datasources. |
| **FLAG 8** | **Legacy image-keyed annotations.** Annotations written before the `(image, name)` re-keying have no description and so do not match named reads; they are superseded on the next save. Pre-re-keying test annotations should be re-saved to migrate cleanly. |

**Other pending work:** the 28-channel / 42.68 GB QPTIFF has not been end-to-end tested; QPTIFF
conversion writes a single-resolution tiled BigTIFF (adding pyramid levels would improve viewer
performance); only Mean intensity is preserved from the OMERO quantification table.

---

## 11. Open questions for NYU

Security and privacy questions are consolidated in **`security_review.md` §7**, which is the
document to send to Information Security. The items below are the deployment and product
questions.

### 11.1 For the pathologist / end user (ask first)

The pathologist is the end user rather than the administrator, but is our conduit. → = route to
IT, the OMERO administrator, or compliance.

**P1 — OMERO.web hosting and the plugin (the top fork).** 
***How do you reach OMERO today (URL)?***

***Who runs that server — your lab, a shared imaging **core facility**, or central NYU IT?***

***Is it a shared long-standing instance (be non-disruptive) or dedicated?***

***Would your administrator add a small plugin to that existing OMERO.web and restart it, or require a separate instance?***

***What OMERO version (needs ≥ 5.6)***

**P2 — Data shape in OMERO.** 
***What are the images (WSI QPTIFF/SVS, multiplex/CyCIF; how many channels; roughly how many GB)?***

***How is quantification stored — a CSV attached to the image, an OMERO table, or produced by a separate tool?***
A: An OMERO table

***How is the segmentation mask stored — an attached file/zip, a separate OMERO **label image**, or not in OMERO at all?*** *(This decides FLAG 5.)* 
A: An attached Zarr file

***Do you analyze the same image several times under different names?***
A: They do perform the gating multiple times on the same image (data) until they finalize.

**P3 — Workflow and expectations.** 
***What is an acceptable wait when first opening an image (the segmentation pyramid build takes 1 ~ 2 minutes)?***

***How many users, and any concurrent viewing?***

**P4 — Login and compute.** 
***Do you log in with institutional SSO or an OMERO username/password?*** *(shapes the identity model)* 
A: OMERO username/password

***Is there existing NYU Kubernetes/compute we deploy onto, or must one be provisioned? Who owns it?***
A: We will be requesting NYU Kubernetes access and deploy our Gater app, which the OMERO-Gater plugin will redirect to when the users open the Gater app from the OMERO.web

**P5 — Governance.** 
***Is the data PHI/HIPAA-regulated? De-identified?*** 

***Any restriction on where it may be processed (on-prem only, no cloud)?***

***Who signs off on data use / IRB?***

### 11.2 For NYU IT / infrastructure

- Is OMERO reachable from the Kubernetes cluster on 4064 (and 4063)? Any firewall or VPN between
  them? Is OMERO in the same network zone as the cluster?
- What does our namespace permit — Deployments, Services, Ingresses, PVCs, RBAC Roles and
  ServiceAccounts? What are the resource quotas, and how many concurrent researchers should we
  size for?
- Is there an ingress controller, and can we get a hostname plus a TLS certificate?
- Which container registry can the cluster pull from — public Docker Hub or an NYU-internal
  registry with image-pull secrets?
- What StorageClass would back per-user PVCs, and is it encrypted at rest?
- Is JupyterHub already blessed centrally at NYU? (We could reuse it instead of our own spawner.)
- Is OMERO.web served the standard way (gunicorn + nginx) so the config/restart flow applies?
- Any reverse-proxy or CSP policy that would block the redirect from OMERO.web's domain to the
  Gater hostname? Any cross-origin or cookie considerations?
- Will proxy buffering be disabled for `text/event-stream` on our ingress (needed by the progress
  bar)?

---

## 12. Repository and release strategy

**Fork → PR.** `gater/` is a fork of the lab repository. Work is pushed to the fork and offered
upstream by **pull request**; we do not push to their `main`. Add `upstream` as a read-only remote
and rebase before opening the PR. Set a stable `git config user.name` / `user.email` — commits are
otherwise attributed to a drifting hostname.

**App vs. infrastructure split.** The lab wants the **application features** (OMERO ingestion,
live tiles, render/config storage) — those stay in this fork and go upstream. **Deployment
infrastructure** (`omero-gater` plugin, `gater-spawner`, `docker-compose*.yml`, Kubernetes
manifests) belongs in a **separate private deployment repository** with `main` / `staging` /
`feature` branches. The boundary: *"how to build the Gater image"* is the app fork's `Dockerfile`;
*"how to run the stack"* is the deployment repository.

`omero-gater/` and `gater-spawner/` are currently **not git repositories**, which makes them clean
to consolidate into that private repo when it is created.

**Upstream Docker choice (leaning).** Keep the OMERO dependencies (`omero-py` / `zeroc-ice`) out
of the base `Dockerfile` and `requirements.yml` — the application's OMERO imports are lazy, so
local mode still works without them — and add them in the deployment-repository image that `FROM`s
the base. This keeps the lab's image lean and avoids the ARM64 Ice build for contributors who
don't need OMERO.

**Versioning is purely git-tag-based** (no version file). A tag `vN_gater` *is* the release;
pushing one triggers the DockerHub workflow (which needs DockerHub secrets configured). Tags
`v1.35_gater` and `v1.36_gater` are currently **local only**, deliberately not pushed.

---

## Appendix A — commit history

Chronological, on `feature/omero_gater_deployment`.

**`fac7a4f`** — Initial OMERO integration: the three converters, `data_model.py` refactor for
OMERO data structures, frontend changes to display OMERO data.

**`bde0a76`** — Pyramid resolution applied correctly for the image file.

**`9a9a32a`** — Locks introduced to prevent the request race.

**`52fb1ff`** — OMERO.web ⇄ Gater integration: live-tile bridge, pyramid and deadlock fixes,
progress bar, base-path support.

**`451660b`** — Fixed the quantification-CSV OOM (chunked conversion) and sped up live-tile
serving: the tile LRU cache and a reusable `RawPixelsStore` (~45 ms → ~11 ms per tile).

**`v1.35_gater`** — OMERO-side render/config storage, tile-OOM bound, restore-on-reopen, UI labels.
- Live-tile OOM fix: `_get_omero_tile` reads the residual region in extra-aligned sub-blocks
  (`GATER_OMERO_MAX_RAW_TILE`, default 2048) instead of one unbounded `getTile`. Bit-identical
  output, peak memory ~step² per tile.
- Render/config state mirrored to the OMERO image as JSON FileAnnotations; helpers
  `put_omero_json` / `get_omero_json` / `_jsonable`; dual-write on save, OMERO-primary reads with
  SQLite fallback and seed-on-load.
- Restore-on-reopen: `_restore_config_entry` re-points environment paths and skips the wizard via
  `omero_restore_redirect.html`.
- "Load/Save … Database" buttons relabelled to "OMERO".

**`v1.36_gater`** (`709c37f`) — `(image, name)` re-keying, multi-name discovery, "Start from"
config, auto-restore-on-open, idempotent channel re-apply.
- A Gater datasource on OMERO is now identified by `(image, kind, dataset_name)` rather than image
  alone; the namespace stays stable per kind, the annotation description carries the name. The
  same image can carry multiple named datasources; a re-save replaces only its own annotation and
  sweeps legacy nameless ones.
- `list_omero_config_names` and `inherit_render_state` added; wizard gained the "Start from"
  dropdown.
- `main.js init()` applies saved render state on viewer load.
- `channelList.js applyChannels` rewritten to be idempotent — set connectors per channel, toggle
  active state only when it differs from `this.selections`. Fixed reset-to-default and the
  default↔saved flash on a second run; range restore now correct.
- Companion (not in this repository): the OMERO.web "Open" config selector in `omero-gater/`.

**`aaaa0b5`** — Architecture/workflow documentation.

---

## Appendix B — reference tables

### B.1 Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `OMERO_HOST` | `localhost` | OMERO server hostname |
| `OMERO_PORT` | `4064` | OMERO server port |
| `OMERO_USER` | `root` | OMERO username — **to be replaced by `OMERO_SESSION`** (§9) |
| `OMERO_PASSWORD` | `omero` | OMERO password — **to be replaced by `OMERO_SESSION`** (§9) |
| `GATER_BASE_PATH` | *(empty)* | URL prefix to serve under, e.g. `/user/jsmith` |
| `GATER_OMERO_MAX_RAW_TILE` | `2048` | Cap on the raw region read per OMERO sub-block |
| `GATER_TILE_CACHE_SIZE` | `256` | Tile LRU capacity (0 disables) |

Spawner-side configuration (`GATER_NAMESPACE`, `GATER_IMAGE`, `GATER_PUBLIC_HOST`,
`GATER_BASE_URL`, `GATER_IDLE_TIMEOUT`, `GATER_CULL_INTERVAL`, `GATER_INGRESS_ENABLED`,
`GATER_INGRESS_CLASS`, `GATER_POD_*`) is documented in `gater-spawner/README.md`.

### B.2 Known axes formats

| Axes | Layout | Example source | Indexing |
|---|---|---|---|
| `CYX` | Channel-first | Standard mcmicro OME-TIFF | `arr[c, y:y+h, x:x+w]` |
| `IYX` | Image-first | Multi-page TIFF (tifffile convention) | `arr[i, y:y+h, x:x+w]` |
| `YXS` | Interleaved RGB | OMERO SVS / RGB crops | `arr[y:y+h, x:x+w, s]` |

Detected via `tifffile.TiffFile(path).series[0].axes`, stored in the `_channel_axes` global.

### B.3 Test datasets

| Dataset | Image | Segmentation | Quantification | Channels |
|---|---|---|---|---|
| kidney (OMERO) | `319-3-kidney_1015391.svs_[0]_0.tif` — YXS, uint8, 3827×4006×3 | `ba64dbbc-….zarr` → `.ome.tif` | `omero_table_1112190.csv` | R, G, B |
| tonsil (OMERO) | `20231103_TrainingTonsil_Scan1.unmixed.qptiff` — CYX, uint16, 8×38880×28800 | `6bd29afc-….zarr` → `.ome.tif` | `omero_table_1112191.csv` | DAPI, HLA, CD3, CD68, CD8, Foxp3, PanCK, AF |
| Gater_example_data (mcmicro) | `crop-crc01-097-096.ome.tif` — CYX, uint16, 43×2000×2000 | same file | `crop-quantification-crc01-097-096.csv` | 43 markers |

### B.4 Issues resolved during Phase 1

1. **`save_config` IndexError** — `convertOmeTiff()` assumed shape `(C, Y, X)`, so a YXS kidney
   image `(3827, 4006, 3)` was read as having 3827 channels. Fixed with axes-aware dimension
   detection.
2. **`get_database_description` crash** — the same YXS issue in `load_datasource()`. Fixed with
   axes-aware level selection, a YXS→CYX transpose, and the `_channel_axes` global.
3. **Black image** (segmentation outlines visible, no tissue) — ViaWebGL rejects uint8 textures
   *and* uint8 values occupy 0.4% of the uint16 range. Fixed by casting to uint16 and scaling
   ×257 in both tile serving and histogram computation.
4. **"No image channel file found"** in the MCMICRO upload form — the frontend passed a directory
   path as the `image` value. Fixed by adding directory scanning to the backend.
5. **Dropdowns listing non-OME-TIFF files** — filter reverted to `.ome.tif`/`.ome.tiff` only,
   extended to discover `.zarr` directories.
6. **Path validation showing "verified" for random directories** — the old two-step validation
   marked any existing directory valid. Replaced with `/check_mc_output_folder`, which requires
   the directory to contain both an `.ome.tif` and a `.csv`.
7. **`ValueError: data too large for standard TIFF file`** — a 16.69 GB QPTIFF exceeded the 4 GB
   limit. Fixed with `bigtiff=True`, tiled output and the disk-backed memmap.
8. **Container OOM-kill on zoom** (exit 137) — the unbounded tile read described in §5.3. Fixed by
   the extra-aligned sub-block loop.
