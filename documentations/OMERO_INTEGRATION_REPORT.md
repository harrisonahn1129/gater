# OMERO-to-Gater Data Pipeline: Integration Report

## Project Overview

**Goal:** Extend the Gater web application — a cell-level microscopy data visualization and gating tool built on ViaWebGL/OpenSeadragon — to accept data directly from the OMERO bioimage management system. Previously, Gater only supported the mcmicro pipeline output format. This work enables a complete OMERO-to-Gater data pipeline covering three data types: multiplexed images (QPTIFF), segmentation masks (OME-NGFF zarr), and quantification tables (OMERO CSV).

**Repository:** `gater/` (fork of the Minerva Analysis platform)

**Environment:** Python 3.9 (`minerva_analysis` conda env), Flask backend, D3/WebGL frontend.

---

## 1. Problem Statement

The OMERO quantification system produces data in formats fundamentally incompatible with Gater's expected inputs:

| Data Type | Gater/mcmicro Expected | OMERO Actual |
|---|---|---|
| **Image** | OME-TIFF (CYX, uint16) | QPTIFF (CYX, uint16, often >4 GB) |
| **Segmentation** | OME-TIFF (pyramidal) | OME-NGFF zarr (5D: TCZYX) |
| **Quantification CSV** | 1 column per channel (`CellID`, `DAPI`, `CD3`, ...) | 8 stats per channel (`object`, `DAPI_Mean_intensity`, `DAPI_Max_intensity`, ...) |
| **Column Naming** | `CellID`, `X_centroid`, `Y_centroid` | `object`, `Centroid_x`, `Centroid_y` |
| **Morphology** | `MajorAxisLength`, `MinorAxisLength` | `Major_axis`, `Minor_axis` |

Additionally, OMERO images include interleaved RGB crops (YXS axes, uint8) that Gater's rendering pipeline was not equipped to handle, since the WebGL shader and tile server assumed CYX uint16 data exclusively.

---

## 2. New Files Created

### 2.1 `minerva_analysis/server/utils/qptiff_to_ometiff.py`

**Purpose:** Converts QPTIFF images (common output from Akoya/Vectra digital pathology scanners) to tiled BigTIFF that Gater's tile server can read.

**Key design decisions:**
- **BigTIFF output** (`bigtiff=True`): QPTIFF files routinely exceed the 4 GB standard TIFF limit (test files: 16.69 GB for 8-channel, 42.68 GB for 28-channel).
- **Disk-backed memmap**: Instead of loading the entire image into RAM, channels are read one-by-one from a zarr store into a temporary `numpy.memmap` file on disk. This reduces peak RAM from ~17 GB to ~2 GB per channel. The memmap is passed directly to `tifffile.TiffWriter`, which reads tiles from it on demand during the write phase.
- **Tiled output** (`tile=(1024, 1024)`): Enables efficient tile-based access by the OpenSeadragon viewer without loading entire channels.
- **CYX series grouping**: Writes the 3D memmap with `metadata={'axes': 'CYX'}` so tifffile groups all pages into a single series — required by `convertOmeTiff()` in `data_model.py`.
- **No OME XML channel names needed**: `convertOmeTiff()` generates channel names from the filename (e.g., `TrainingTonsil_0`, `TrainingTonsil_1`), not from OME metadata. Channel-to-CSV matching happens later during the `save_config` step.

**Functions:**
- `qptiff_to_ometiff(input_path, output_path)` — Main converter. Handles CYX, IYX, and YXS input layouts.
- `_get_cyx_info(shape, axes)` — Determines (num_channels, height, width, is_yxs) from tifffile's axes metadata string.
- `get_channel_names(path)` — Extracts biomarker names from QPTIFF via `qptifffile` library (optional dependency).

### 2.2 `minerva_analysis/server/utils/omero_csv_to_mcmicro.py`

**Purpose:** Converts OMERO quantification CSV tables to the mcmicro format that Gater's config generation, channel matching, and normalization pipelines expect.

**Key design decisions:**
- **Auto-detection**: `is_omero_csv(header)` checks if the first column is `object` AND at least one column ends with `_Mean_intensity`. This ensures mcmicro CSVs (which start with `CellID`) pass through unchanged — no false positives.
- **Mean-only extraction**: Of the 8 statistical columns per channel (Mean, Max, Min, P25, Median, P75, P95, Std_dev), only the Mean intensity is kept, matching mcmicro's single-value-per-channel convention.
- **Suffix stripping handles multi-word names**: `Pan_Cytokeratin_Mean_intensity` → `Pan_Cytokeratin` (strips only the `_Mean_intensity` suffix, not arbitrary underscored segments).
- **In-place conversion**: When called from the upload route, overwrites the server-side copy so all downstream code sees mcmicro format.

**Column transformations:**
```
object           → CellID
Centroid_x       → X_centroid
Centroid_y       → Y_centroid
R_Mean_intensity → R       (channel columns — suffix stripped)
Major_axis       → MajorAxisLength
Minor_axis       → MinorAxisLength
```
All other columns (prob, geometry, Bbox_*, tile_index, *_Max_intensity, *_Std_dev_intensity, etc.) are dropped.

### 2.3 `minerva_analysis/server/utils/omero_zarr_to_ometiff.py`

**Purpose:** Converts OMERO OME-NGFF segmentation masks (zarr format with 5D TCZYX arrays and pre-computed resolution pyramids) to SubIFD-based pyramidal OME-TIFF.

**Key design decisions:**
- **Pre-computed pyramids**: OMERO zarr stores already contain multiple resolution levels (e.g., levels 0-5). The converter reads each level directly rather than recomputing downsampled pyramids, saving significant computation time.
- **Memory-efficient tile iteration**: `_zarr_tile_iter()` yields tiles from 5D zarr arrays in row-major order, avoiding full-resolution array loading.
- **zlib compression**: Reduces output file size for segmentation masks (which are sparse integer arrays).
- **5D→2D extraction**: Extracts the YX plane from TCZYX by indexing `[0, 0, 0, :, :]` (first timepoint, first channel, first Z-slice).

---

## 3. Modified Files

### 3.1 `minerva_analysis/server/routes/import_routes.py`

**Changes span the entire upload pipeline:**

#### a) Format conversion routing (lines 28-68)
New helper `_convert_channel_to_ometiff()` acts as a unified dispatcher: given any supported image format (.svs, .ndpi, .qptiff, .tif, .ome.tif), it routes to the appropriate converter or passes through native TIFFs unchanged. Extension sets `_WSI_EXTENSIONS`, `_QPTIFF_EXTENSIONS`, and `_NATIVE_EXTENSIONS` make this easily extensible.

#### b) OMERO CSV auto-detection in upload flow (lines 306-310)
After the CSV is copied to the server data directory, the header is probed with `is_omero_csv()`. If detected, `omero_csv_to_mcmicro()` converts it in-place before the header is parsed for config generation. This is transparent to the rest of the pipeline.

#### c) Extended `listNotMarkers` (lines 366-382)
The list of non-marker column names (used to separate intensity channels from metadata in the CSV Gating panel) was extended to include OMERO-specific columns: `prob`, `geometry`, `centroid`, `Bbox_min_x`, `Bbox_min_y`, `Bbox_max_x`, `Bbox_max_y`, `tile_index`, `orig_object`, `Perimeter`, `Centroid_x`, `Centroid_y`, `Longest_axis`, `Convexity`, `Compactness_circle`, `Compactness_square`, `Area_convex`, `Min_rot_rect`, `Elongation`, `Major_axis`, `Minor_axis`, `Circular_diameter`, `Euler_number`. This ensures morphology/metadata columns don't appear as gating channels.

#### d) MCMICRO path validation refactor (lines 675-700)
Replaced the `/check_mc_channel_file_existence` route with `/check_mc_output_folder`. The old route only checked for image files and used the same input element for both `path` and `image` parameters (a bug). The new route validates that the directory contains **both** `.ome.tif`/`.ome.tiff` **and** `.csv` files, returning a structured `{path_exists, has_ome_tif, has_csv}` response that the frontend uses to show specific error messages.

#### e) Dropdown file filter (lines 628-644)
`get_mc_segmentation_file_list` was refined to list only `.ome.tif`/`.ome.tiff` files (not plain `.tif`), since those are the only formats the viewer can render directly. Also extended to discover `.zarr` directories for OMERO segmentation masks.

#### f) Directory-aware path validation (lines 675-700)
`check_mc_channel_file_existence` was updated to handle directory paths (not just file paths) by scanning directory contents for image files. This fixed a bug where the MCMICRO form's `checkMCOutputFolder()` JS function passed the directory path as the `image` value.

### 3.2 `minerva_analysis/server/models/data_model.py`

This file is the core data pipeline: it loads images, serves tiles to the WebGL viewer, and computes histograms for the channel sliders. Multiple changes were needed to handle OMERO data characteristics.

#### a) Global `_channel_axes` tracking (line 38)
Added a module-level variable `_channel_axes` that stores the tifffile axes string (e.g., `'CYX'`, `'IYX'`, `'YXS'`) for the currently loaded channel image. This is set in `load_datasource()` and read by `generate_zarr_png()` so tile extraction uses the correct indexing order.

**Why:** OMERO RGB crops use YXS (interleaved) axes layout where pixels are stored as `[y][x][channel]`, while mcmicro images use CYX (channel-first) where pixels are stored as `[channel][y][x]`. Without tracking the axes, tile extraction indexes the wrong dimensions.

#### b) YXS-aware level selection in `load_datasource()` (lines 81-98)
When finding a suitable resolution level for histogram computation, the code now uses axes-aware dimension indices (`_y_idx`, `_x_idx`) instead of hardcoded positions. For YXS data, Y is at index 0 and X at index 1; for CYX data, Y is at index 1 and X at index 2.

After level selection, YXS data is transposed to CYX (`np.transpose(zarray, (2, 0, 1))`) so the downstream histogram computation code works unchanged.

#### c) uint8 → uint16 scaling (lines 100-103, 878-882)
**The most critical rendering fix.** OMERO RGB crops are uint8 (0-255), but the ViaWebGL fragment shader (`frag.glsl`) encodes pixel values into a uint16 range via `(pixel.r * 255 + pixel.g) / 65535.0`, and the client hardcodes `imageBitRange = [0, 65536]` in `dataLayer.js:12`.

Without scaling, uint8 values 0-255 map to 0.000–0.004 in the shader — effectively invisible (black image). The fix multiplies by 257 (`0→0, 255→65535`), which is the exact integer mapping since `257 = 65535/255`.

This scaling is applied in **two places** to maintain consistency:
1. **Tile serving** (`generate_zarr_png()`, line 882): `tile.astype(np.uint16) * np.uint16(257)` — so the WebGL viewer receives values in the full uint16 range.
2. **Histogram computation** (`load_datasource()`, line 103): `np.asarray(zarray).astype(np.uint16) * np.uint16(257)` — so the channel slider min/max values match the scaled tile values.

#### d) YXS-aware tile extraction in `generate_zarr_png()` (lines 868-877)
The tile serving function now checks `_channel_axes` to determine indexing order:
- **YXS (interleaved)**: `channels[iy:iy+h, ix:ix+w, channel_num]`
- **CYX (channel-first)**: `channels[channel_num, iy:iy+h, ix:ix+w]`

This applies to both `zarr.Array` (non-pyramidal, single level) and `zarr.Group` (pyramidal, multi-level) access patterns.

#### e) Axes-aware `convertOmeTiff()` (lines 913-943)
The config generation function that extracts image dimensions now uses `channel_io.series[0].axes` metadata to identify the C, Y, X dimension indices instead of assuming a fixed `(C, Y, X)` shape order. This fixed an `IndexError` where a YXS image with shape `(3827, 4006, 3)` was misinterpreted as having 3827 channels.

Tile sizes are capped to 1024 — non-tiled images have chunks equal to full image dimensions, which would create single-tile requests too large for the browser.

#### f) Zarr segmentation mask support in `convertOmeTiff()` (lines 952-958)
When the segmentation path ends with `.zarr`, the function calls `omero_zarr_to_ometiff()` to convert it to OME-TIFF before proceeding with the standard pyramid assembly.

### 3.3 `minerva_analysis/client/src/js/services/importFormValidation.js`

#### a) Rewritten `checkMCOutputFolder()` (lines 38-77)
Previously used a two-step validation (`checkPathExistence` → `checkChannelExistence`) where:
1. `checkPathExistence` would mark the field green (valid) immediately if the directory existed — even before checking contents.
2. `checkChannelExistence` read `image` from the same input element as `path` (a bug), so it just checked if the path string had an image file extension.

**Result:** Any existing directory showed as "verified", even with random paths containing no relevant files.

The rewrite makes a single call to `/check_mc_output_folder` and only marks the field as valid when all three conditions are met: path exists, contains `.ome.tif`/`.ome.tiff`, and contains `.csv`. Specific error messages are shown for each failure case:
- "Please provide a valid path." — directory doesn't exist
- "No .ome.tif or .csv files found in this directory."
- "No .ome.tif file found in this directory."
- "No .csv file found in this directory."

#### b) Removed unused functions
`checkChannelExistence()` and `checkPathExistence()` were deleted since they are no longer referenced anywhere.

### 3.4 `minerva_analysis/client/templates/upload.html`

#### a) Dropdown validation handlers (lines 125, 138)
Changed `onchange="checkChannelExistence(this)"` to `onchange="checkFileExistence(this)"` on the image and segmentation dropdown `<select>` elements. The old handler called the removed `/check_mc_channel_file_existence` route; the new handler uses `/check_file_existence` which properly validates that the selected file exists on disk.

### 3.5 `minerva_analysis/data/config.json`

Updated with dataset configurations for the OMERO kidney test data, demonstrating the full pipeline working end-to-end. The kidney entry shows the converted paths: OMERO zarr segmentation mask → `.ome.tif`, OMERO CSV → mcmicro format, SVS crop → tile-served via zarr.

---

## 4. Rendering Pipeline Context

Understanding the rendering pipeline is essential for debugging future image display issues:

```
TIF on disk
  → tifffile opens as zarr store
    → data_model.py:generate_zarr_png() extracts tiles as numpy arrays
      → (uint8 tiles scaled ×257 to uint16 here)
        → data_routes.py:generate_png() encodes as PNG via PIL
          → Browser receives PNG
            → ViaWebGL decodes PNG, uploads as RG8UI texture
              → Fragment shader (frag.glsl): value = (R×255+G)/65535
                → range_clamp(value) maps [sliderMin/65536, sliderMax/65536] → [0, 1]
                  → Pixel displayed
```

**Key constants:**
- `imageBitRange = [0, 65536]` is hardcoded in `dataLayer.js:12`
- Channel slider range `u_tile_range` is `[sliderMin/65536, sliderMax/65536]`
- Pixel value normalized in shader: `(R*255+G)/65535` (note: slight error vs true `R*256+G`, but matches the uint8-packed uint16 encoding)

---

## 5. Known Axes Formats

| Axes String | Layout | Example Source | Indexing |
|---|---|---|---|
| `CYX` | Channel-first | Standard mcmicro OME-TIFF | `arr[c, y:y+h, x:x+w]` |
| `IYX` | Image-first | Multi-page TIFF (tifffile convention) | `arr[i, y:y+h, x:x+w]` |
| `YXS` | Interleaved RGB | OMERO SVS/RGB crops | `arr[y:y+h, x:x+w, s]` |

Detection: `tifffile.TiffFile(path).series[0].axes` returns the axes string. Stored in the global `_channel_axes` variable in `data_model.py`.

---

## 6. Test Datasets

| Dataset | Image | Segmentation | CSV | Channels |
|---|---|---|---|---|
| kidney (OMERO) | `319-3-kidney_1015391.svs_[0]_0.tif` (YXS, uint8, 3827×4006×3) | `ba64dbbc-...zarr` → `.ome.tif` | `omero_table_1112190.csv` | R, G, B |
| tonsil (OMERO) | `20231103_TrainingTonsil_Scan1.unmixed.qptiff` (CYX, uint16, 8×38880×28800) | `6bd29afc-...zarr` → `.ome.tif` | `omero_table_1112191.csv` | DAPI, HLA, CD3, CD68, CD8, Foxp3, PanCK, AF |
| Gater_example_data (mcmicro) | `crop-crc01-097-096.ome.tif` (CYX, uint16, 43×2000×2000) | same file | `crop-quantification-crc01-097-096.csv` | 43 markers (Hoechst0, AF488, anti_CD3, ...) |

---

## 7. Issues Resolved (Chronological)

1. **`save_config` IndexError**: `convertOmeTiff()` assumed shape `(C, Y, X)` but kidney TIF has YXS `(3827, 4006, 3)` → misread 3827 as channel count. Fixed with axes-aware dimension detection.

2. **`get_database_description` crash on `zarray[image_layer]`**: YXS issue propagated to `load_datasource()`. Fixed with axes-aware level selection, YXS→CYX transpose, and global `_channel_axes` tracking.

3. **Black image (segmentation outlines visible but no tissue)**: Two sub-issues:
   - ViaWebGL rejects uint8 textures (only u16/u32). Fixed by casting to uint16.
   - uint8 values (0-255) occupy only 0.4% of the uint16 range (0-65535), appearing invisible against `imageBitRange=[0,65536]`. Fixed by scaling ×257 in both tile serving and histogram computation.

4. **"No image channel file found" error in MCMICRO upload form**: `checkMCOutputFolder()` JS function passed directory path as `image` value to backend, which only checked file suffixes. Fixed by adding directory scanning to backend.

5. **MCMICRO dropdown showing non-OME-TIFF files**: Reverted file filter to only list `.ome.tif`/`.ome.tiff` files in dropdowns.

6. **Path validation showing "verified" for random directories**: Rewrote frontend validation to check for both `.ome.tif` and `.csv` files via new `/check_mc_output_folder` backend route.

7. **`ValueError: data too large for standard TIFF file`**: QPTIFF converter wrote standard TIFF, but 16.69 GB exceeds 4 GB limit. Fixed with `bigtiff=True`, tiled output, and disk-backed memmap for memory efficiency.

---

## 8. Architecture Decisions

- **In-place CSV conversion**: OMERO CSVs are converted to mcmicro format at upload time rather than adding OMERO-aware code throughout the pipeline. This keeps the existing config generation, channel matching, and normalization code unchanged.

- **Disk-backed memmap for QPTIFF**: Writing tiled BigTIFF requires a 3D CYX array (tifffile limitation for single-series output). Rather than loading 17-43 GB into RAM, a temporary memmap file on disk backs the array. tifffile reads tiles from the memmap on demand during the write phase.

- **Axes tracking via global**: `_channel_axes` is stored as a module-level global in `data_model.py` rather than passed through every function call. This matches the existing pattern used by `channels`, `seg`, `zarray`, and `config` globals in the same module.

- **×257 scaling, not bit-shifting**: `uint8 × 257 = uint16` maps `0→0, 255→65535` exactly (since `65535/255 = 257`). This preserves the full dynamic range rather than simple `<< 8` bit-shifting which would map `255→65280`.

---

## 9. File Reference

| File | Status | Purpose |
|---|---|---|
| `server/utils/qptiff_to_ometiff.py` | **NEW** | QPTIFF → BigTIFF converter (memmap, tiled) |
| `server/utils/omero_csv_to_mcmicro.py` | **NEW** | OMERO CSV → mcmicro format converter |
| `server/utils/omero_zarr_to_ometiff.py` | **NEW** | OME-NGFF zarr → pyramidal OME-TIFF converter |
| `server/routes/import_routes.py` | **MODIFIED** | Upload pipeline: format detection, conversion routing, validation |
| `server/models/data_model.py` | **MODIFIED** | Tile serving: YXS axes, uint8 scaling, zarr segmentation support |
| `client/src/js/services/importFormValidation.js` | **MODIFIED** | MCMICRO path validation rewrite |
| `client/templates/upload.html` | **MODIFIED** | Dropdown validation handlers |
| `data/config.json` | **MODIFIED** | Dataset configurations for OMERO + mcmicro test data |

---

## 10. Pending / Future Work

- **Large QPTIFF upload time**: The tonsil QPTIFF (16.69 GB) takes several minutes to convert. Progress feedback during conversion could be improved.
- **Pyramidal output for QPTIFF**: Currently writes a single-resolution tiled BigTIFF. Adding pyramid levels would improve viewer performance for very large images.
- **Additional OMERO CSV statistics**: Currently only Mean intensity is preserved. If P75 or other percentiles are useful for gating, the converter could be extended to keep additional columns.
- **The second QPTIFF** (`20230504-228B-custom_Scan1.qptiff`, 28 channels, 42.68 GB) has not been end-to-end tested through the upload flow yet.

---

## 11. Commit Log

Chronological log of tagged commits on `feature/omero_gater_deployment` (newest appended at the bottom). Each entry lists the tag and what it implemented.

### (baseline) `451660b` — Fix OMERO quant-CSV OOM and speed up live-tile serving (cache + store reuse)
Prior committed state: live-tile LRU cache, reusable `RawPixelsStore`, quant-CSV OOM fix.

### `v1.35_gater` — OMERO-side render/config storage, tile-OOM bound, restore-on-reopen, OMERO UI labels
- **Live-tile OOM fix (Fix a)** — `_get_omero_tile` now reads the residual region in **extra-aligned sub-blocks** (new `_OMERO_MAX_RAW_TILE`, default 2048, env `GATER_OMERO_MAX_RAW_TILE`) instead of one unbounded `getTile`. Output is bit-identical to the old single read, but peak memory is ~step² pixels/tile regardless of zoom — fixes the tonsil zoom OOM-kill (exit 137) with seg + channel on.
- **OMERO-side render/config storage** — a datasource's render state (channel colors/ranges, gating thresholds/lassos) and its config entry are mirrored to the OMERO image as **JSON FileAnnotations** (namespaces `gater.vida.nyu/render-state/{channel_list,gating_list}`, `gater.vida.nyu/config`). New helpers `put_omero_json` / `get_omero_json` (best-effort, reconnect-retry, **replace-by-namespace** so saves never accumulate duplicates) and `_jsonable` (strict-JSON: numpy→native, NaN→null). Dual-write on save (`save_channel_list`, `save_gating_list`); **OMERO-primary reads** with local-SQLite fallback + seed-on-load (`get_saved_channel_list`, `get_saved_gating_list`); config entry pushed in `save_config`.
- **Restore-on-reopen** — `open_from_omero_run` reads back `gater_config.json`; `_restore_config_entry` re-points env-specific paths (CSV, segmentation, tile routes) at the current host's files and **skips the channel-match wizard**, redirecting straight to the viewer via new template `omero_restore_redirect.html`. Render state then restores via the OMERO-primary reads. (Limitation: phenotype/cluster data not restored — not re-downloaded.)
- **UI relabels** — Channels + CSV Gating "Load/Save … Database" buttons and their save alerts now read "OMERO".
- **Docs** — `DEPLOYMENT_PLAN.md` §9 flags the slow datasource-switch (single-slot globals → full-CSV reload, plus the OMERO annotation round-trips this work added).
- **Files:** `data_model.py`, `import_routes.py`, `client/templates/omero_restore_redirect.html` (new), `client/templates/index.html`, `client/src/js/views/csvGatingList.js`, `client/src/js/views/channelList.js`, `DEPLOYMENT_PLAN.md`, `OMERO_INTEGRATION_REPORT.md`.

### `v1.36_gater` — `(image, name)` re-keying, multi-name discovery, "start from" config, auto-restore-on-open, idempotent channel re-apply
- **`(image, name)` re-keying** — a Gater datasource on OMERO is now identified by `(image, kind, dataset_name)` instead of image alone. Namespace stays stable per kind (one indexed discovery query); the annotation **description** carries the exact dataset name. `put_omero_json`/`get_omero_json` gained a `dataset_name` param; put replaces only the same-name annotation (+ sweeps legacy nameless ones), so **the same image can carry multiple named datasources** while a re-save of one name replaces only itself. Helper `_gater_ann_name_of`.
- **Multi-name support + "Start from" seeding** — `list_omero_config_names(image_id)` lists saved configs on an image; `inherit_render_state(image_id, src, dst)` copies channel_list + gating_list from one name into another as an independent copy. Gater wizard gained a **"Start from" dropdown** (`channel_match.html` + `channelMatch.js` pass `baseConfig`; `save_config` calls `inherit_render_state`) to seed a new datasource from a prior config on the same image.
- **Auto-restore-on-open** — `main.js init()` now applies saved render state on viewer load (`applyChannels('db')` + `applyGates('db')` after all event bindings), so a restored datasource comes back as saved without a manual "Load from OMERO" click.
- **Idempotent channel re-apply** — rewrote `channelList.js applyChannels` to be safe to re-run: set range/color connectors per channel and toggle active state ONLY when it differs from `this.selections` (fixes reset-to-default + default↔saved flash on the second run); range restore now correct.
- **Docs** — `DEPLOYMENT_PLAN.md` §11 records the render/config-storage progress and 8 follow-up FLAGS (color restore, seg-mask vanish, slow switch, config paths→OMERO, no-persist live-stream, celltype/cluster restore, root ownership, legacy annotations).
- **Companion (NOT in this repo):** the OMERO.web-side "Open" config selector lives in `omero-gater/` (a separate, non-git-tracked directory) — `views.py` `_saved_gater_configs` + `gater_tab.html` dropdown; commit/track separately.
- **KNOWN-OPEN (see §11.3 flags):** channel **color** restore still shows default (deprioritized); **"Load Gating from OMERO" makes the segmentation mask vanish** (WebGL render desync).
- **Files:** `data_model.py`, `import_routes.py`, `client/src/js/main.js`, `client/src/js/views/channelList.js`, `client/src/js/views/channelMatch.js`, `client/templates/channel_match.html`, `DEPLOYMENT_PLAN.md`, `OMERO_INTEGRATION_REPORT.md`.
