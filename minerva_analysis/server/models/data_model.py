from sklearn.neighbors import BallTree
from sklearn.preprocessing import MinMaxScaler
import numpy as np
import pandas as pd
from PIL import ImageColor
import json
import os
import io
import tempfile
from pathlib import Path
from pathlib import PurePath
from ome_types import from_xml
from minerva_analysis import config_json_path, data_path, cwd_path
from minerva_analysis.server.utils import pyramid_assemble, pyramid_upgrade
from minerva_analysis.server.utils.omero_zarr_to_ometiff import omero_zarr_to_ometiff
from minerva_analysis.server.models import database_model
from minerva_analysis.server.utils import smallestenclosingcircle
import matplotlib.path as mpltPath
from itertools import chain
import dateutil.parser
import time
import pickle
import tifffile as tf
import re
import zarr
import cv2
from sklearn.mixture import GaussianMixture
from scipy.stats import norm
from skimage.measure import block_reduce

import threading

ball_tree = None
database = None
source = None
config = None
seg = None
zarray = None
channels = None
metadata = None
_channel_axes = None  # tifffile axes string (e.g. 'CYX', 'IYX', 'YXS')
_load_lock = threading.Lock()  # Prevent concurrent load_datasource calls
_omero_lock = threading.Lock()  # Serialize OMERO access (BlitzGateway is not thread-safe)

# --- OMERO integration ---
_omero_conn = None
_omero_pixels_cache = {}  # image_id -> {pid, dtype, levels}
_omero_rps_cache = {}     # image_id -> reusable RawPixelsStore (per connection)
_omero_last_used = 0.0    # monotonic-ish time of last connection use
_OMERO_IDLE_TIMEOUT = 45  # seconds; reconnect fresh after this much idle
# Max raw region (pixels/side) read off OMERO in one getTile before we tile the
# read into sub-blocks. Caps peak per-tile memory so a coarse-zoom residual read
# on a gigapixel plane can't OOM-kill the container. Env-overridable.
_OMERO_MAX_RAW_TILE = int(os.environ.get('GATER_OMERO_MAX_RAW_TILE', '2048'))

# --- Live-tile LRU cache ---
# Caches the final per-(image, channel, level, tile) numpy array so repeated
# requests (panning back, toggling a channel on/off, re-rendering) never touch
# OMERO. Guarded by its OWN lock (not _omero_lock), so cache hits are fully
# concurrent across server threads instead of serializing behind OMERO.
from collections import OrderedDict as _OrderedDict
_tile_cache = _OrderedDict()
_tile_cache_lock = threading.Lock()
# ~2 MB per uint16 1024x1024 tile; 256 -> ~0.5 GB ceiling. Env-overridable.
_TILE_CACHE_MAX = max(0, int(os.environ.get('GATER_TILE_CACHE_SIZE', '256')))


def _tile_cache_get(key):
    with _tile_cache_lock:
        val = _tile_cache.get(key)
        if val is not None:
            _tile_cache.move_to_end(key)
        return val


def _tile_cache_put(key, val):
    if _TILE_CACHE_MAX <= 0:
        return
    with _tile_cache_lock:
        _tile_cache[key] = val
        _tile_cache.move_to_end(key)
        while len(_tile_cache) > _TILE_CACHE_MAX:
            _tile_cache.popitem(last=False)


def _clear_omero_caches():
    """Close and drop cached RawPixelsStores + pixel metadata. Call whenever the
    OMERO connection is (re)dropped — the stores are bound to the OMERO session
    and become invalid across a reconnect. (The tile cache is NOT cleared: tiles
    are keyed by image content, which does not change across reconnects.)
    Must be called while holding _omero_lock."""
    for rps in _omero_rps_cache.values():
        try:
            rps.close()
        except Exception:
            pass
    _omero_rps_cache.clear()
    _omero_pixels_cache.clear()


def _ensure_omero_connection():
    """Return a connected BlitzGateway, reusing or reconnecting as needed.

    Must be called while holding _omero_lock.
    """
    global _omero_conn, _omero_last_used
    now = time.time()
    # Reuse the cached connection only if it was used recently. After an idle
    # gap the Ice connection may have silently dropped, and *probing* a dead
    # connection (isConnected/keepAlive) can HANG with no client timeout — which
    # is what stalled save_config. So we never probe: if idle too long, we drop
    # the connection and reconnect fresh (a fast, reliable operation).
    if _omero_conn is not None and (now - _omero_last_used) < _OMERO_IDLE_TIMEOUT:
        _omero_last_used = now
        return _omero_conn
    if _omero_conn is not None:
        try:
            _omero_conn.close()
        except Exception:
            pass
        _omero_conn = None
        _clear_omero_caches()

    from omero.gateway import BlitzGateway
    # Host/port/credentials are env-configurable so the containerized app can
    # reach the OMERO server by its service name (OMERO_HOST=omeroserver),
    # while a host-run app defaults to localhost.
    host = os.environ.get('OMERO_HOST', 'localhost')
    port = int(os.environ.get('OMERO_PORT', '4064'))
    user = os.environ.get('OMERO_USER', 'root')
    password = os.environ.get('OMERO_PASSWORD', 'omero')
    _omero_conn = BlitzGateway(user, password, host=host, port=port)
    if not _omero_conn.connect():
        raise ConnectionError(
            "Failed to connect to OMERO server at %s:%s" % (host, port))
    _omero_last_used = now
    print("Connected to OMERO server at %s:%s" % (host, port))
    return _omero_conn


# OMERO pixel type -> numpy dtype. OMERO's raw pixel data is big-endian.
_OMERO_NP_DTYPE = {
    'int8': '>i1', 'uint8': '>u1', 'int16': '>i2', 'uint16': '>u2',
    'int32': '>i4', 'uint32': '>u4', 'float': '>f4', 'double': '>f8',
}


def _omero_pixel_cache(image_id):
    """Cache (per image) the pixels id, dtype, and resolution-level dims read
    from OMERO's own pyramid. Must be called while holding _omero_lock."""
    cache = _omero_pixels_cache.get(image_id)
    if cache is None:
        conn = _ensure_omero_connection()
        image = conn.getObject('Image', image_id)
        if image is None:
            raise ValueError("OMERO image %s not found" % image_id)
        pixels = image.getPrimaryPixels()
        ptype = pixels.getPixelsType().getValue()
        rps = conn.c.sf.createRawPixelsStore()
        rps.setPixelsId(pixels.getId(), False)
        # getResolutionDescriptions(): index 0 = full res, last = smallest.
        levels = [(d.sizeX, d.sizeY) for d in rps.getResolutionDescriptions()]
        rps.close()
        cache = {'pid': pixels.getId(),
                 'dtype': _OMERO_NP_DTYPE.get(ptype, '>u2'),
                 'levels': levels}
        _omero_pixels_cache[image_id] = cache
    return cache


def _get_rps(image_id):
    """Return a reusable RawPixelsStore for image_id, created once per
    connection. Reusing the store avoids ~35 ms/tile of createRawPixelsStore +
    setPixelsId overhead (measured ~45 ms -> ~11 ms per tile). Cleared on
    reconnect via _clear_omero_caches(). Must be called while holding
    _omero_lock (the store is stateful: setResolutionLevel is set per tile)."""
    rps = _omero_rps_cache.get(image_id)
    if rps is None:
        conn = _ensure_omero_connection()
        cache = _omero_pixel_cache(image_id)
        rps = conn.c.sf.createRawPixelsStore()
        rps.setPixelsId(cache['pid'], False)
        _omero_rps_cache[image_id] = rps
    return rps


def _get_omero_tile(image_id, channel_num, x, y, w, h, level=0):
    """Fetch a single-channel 2D tile from OMERO for the client's UNIFORM
    power-of-2 pyramid.

    The Gater client and the segmentation .ome.tif use a uniform pyramid where
    server ``level`` k means "full resolution downsampled by 2**k", and (x, y)
    are pixel offsets in that level-k coordinate space. OMERO's own stored
    pyramid, however, is typically SPARSE and non-power-of-two (e.g. an SVS
    whole slide keeps levels at 1x, 1/4, 1/16, 1/32 only). Mapping the client
    level straight onto the OMERO level index therefore mis-scales every level
    except full-res and leaves most tiles blank once zoomed in.

    So express the requested tile as a full-resolution pixel region, read it
    from the finest OMERO level no coarser than needed, then block-average the
    residual factor down to the target tile. This reconstructs exactly the
    uniform pyramid the client and segmentation share, keeping channel tiles
    aligned with the segmentation at every zoom -- and still size-safe on
    gigapixel slides (a level-k tile only ever reads ~w*h pixels off OMERO).

    Results go through an LRU cache keyed by the exact request, so re-rendering
    the same tile (channel toggle, pan back, redraw) is served from memory
    without touching OMERO or the OMERO lock.
    """
    key = (image_id, channel_num, level, x, y, w, h)
    cached = _tile_cache_get(key)
    if cached is not None:
        return cached

    with _omero_lock:
        cache = _omero_pixel_cache(image_id)
        levels = cache['levels']
        n = len(levels)
        full_w = float(levels[0][0])

        target_ds = float(2 ** int(level))       # client downsample vs full res
        # OMERO level j downsample factor relative to full res (~[1, 4, 16, 32]).
        ds = [full_w / lw for (lw, lh) in levels]
        # Finest OMERO level no finer than needed: largest ds[j] <= target_ds, so
        # we read the least data and block-average the residual (>=1x) down.
        j = 0
        for jj in range(n):
            if ds[jj] <= target_ds + 1e-6:
                j = jj
            else:
                break
        extra = max(1, int(round(target_ds / ds[j])))   # residual downsample

        lw, lh = levels[j]
        # Full-res region this tile covers, mapped into OMERO level-j coords.
        xj = int(round(x * target_ds / ds[j]))
        yj = int(round(y * target_ds / ds[j]))
        wj = min(int(round(w * target_ds / ds[j])), lw - xj)
        hj = min(int(round(h * target_ds / ds[j])), lh - yj)
        if wj <= 0 or hj <= 0:
            return np.zeros((1, 1), dtype=cache['dtype'])
        # Reuse the per-image pixel store instead of recreating it per tile.
        rps = _get_rps(image_id)
        # setResolutionLevel(): 0 = smallest, n-1 = full res (reversed vs levels).
        rps.setResolutionLevel((n - 1) - j)

        # Bound the raw region read off OMERO. At coarse client levels the
        # residual region (wj x hj at OMERO level j) can span most of the plane
        # -- hundreds of MB to GBs -- which np.frombuffer + block_reduce roughly
        # triples, OOM-killing the container. So read it in EXTRA-ALIGNED
        # sub-blocks of at most ~_OMERO_MAX_RAW_TILE per side, block-averaging
        # each into a preallocated output. Because every sub-block starts on an
        # `extra` boundary and interior blocks are exact multiples of `extra`,
        # only the trailing block is edge-padded -- exactly as a single
        # block_reduce over the whole region pads -- so the output is
        # bit-identical to the old single read. Peak memory is ~step^2 pixels
        # per tile regardless of zoom level, and it works even when OMERO
        # exposes no pyramid at all.
        out_h = -(-hj // extra)   # ceil(hj / extra)
        out_w = -(-wj // extra)   # ceil(wj / extra)
        out = np.empty((out_h, out_w), dtype=cache['dtype'])
        step = max(extra, (_OMERO_MAX_RAW_TILE // extra) * extra)
        for ry in range(0, hj, step):
            bh = min(step, hj - ry)
            oy = ry // extra
            for rx in range(0, wj, step):
                bw = min(step, wj - rx)
                ox = rx // extra
                buf = rps.getTile(0, channel_num, 0, xj + rx, yj + ry, bw, bh)
                sub = np.frombuffer(buf, dtype=cache['dtype']).reshape(bh, bw)
                if extra > 1:
                    sub = block_reduce(
                        sub, (extra, extra), np.mean).astype(cache['dtype'])
                out[oy:oy + sub.shape[0], ox:ox + sub.shape[1]] = sub

    _tile_cache_put(key, out)
    return out


def download_omero_annotation(ann_id, dest_path):
    """Download an OMERO FileAnnotation's file content to dest_path.

    Retries once if the Ice connection drops mid-transfer (large files can
    outlast an idle/stale session); the retry forces a fresh reconnect.
    """
    global _omero_conn
    last_exc = None
    for attempt in range(2):
        try:
            with _omero_lock:
                conn = _ensure_omero_connection()
                ann = conn.getObject("FileAnnotation", int(ann_id))
                if ann is None:
                    raise ValueError(
                        "OMERO FileAnnotation %s not found" % ann_id)
                with open(dest_path, 'wb') as fh:
                    for chunk in ann.getFileInChunks():
                        fh.write(chunk)
            return dest_path
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 - connection loss etc.
            last_exc = exc
            with _omero_lock:
                try:
                    if _omero_conn is not None:
                        _omero_conn.close()
                except Exception:
                    pass
                _omero_conn = None
                _clear_omero_caches()
    raise last_exc


# --- OMERO-side render/config storage (JSON FileAnnotations) --------------
# A datasource's render state (channel colors/ranges, gating thresholds and
# lassos) and its config entry are mirrored to OMERO as JSON FileAnnotations
# on the datasource's Image, so the datasource can be restored on any host
# straight from OMERO. Each kind uses a stable namespace, and a re-save
# REPLACES the prior annotation of that namespace instead of accumulating
# duplicates. Every write is best-effort: the local SQLite / config.json store
# stays good enough that an OMERO outage never blocks or breaks a save.
_GATER_NS = {
    'channel_list': 'gater.vida.nyu/render-state/channel_list',
    'gating_list': 'gater.vida.nyu/render-state/gating_list',
    'config': 'gater.vida.nyu/config',
}


def _jsonable(o):
    """Recursively coerce to strict-JSON-safe types: numpy scalars -> native,
    numpy arrays -> lists, and NaN floats -> None (so the output is portable
    JSON any reader can parse, not the non-standard NaN token)."""
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if v == v else None      # NaN -> None (NaN != NaN)
    return o


def _omero_image_id_for(datasource_name):
    """omero_image_id for a datasource from the loaded config, or None (local
    upload / config not yet loaded => nothing to mirror to OMERO)."""
    global config
    try:
        return config[datasource_name].get('omero_image_id')
    except Exception:
        return None


def put_omero_json(image_id, kind, obj):
    """Store obj as a JSON FileAnnotation (namespace _GATER_NS[kind]) on the
    OMERO Image, replacing any prior Gater annotation of that namespace. The
    new annotation is created BEFORE the old ones are removed, so data is never
    lost if the delete fails (readers pick the newest). Best-effort: returns
    True on success, False on any failure -- never raises."""
    global _omero_conn
    ns = _GATER_NS[kind]
    filename = 'gater_%s.json' % kind
    data = json.dumps(_jsonable(obj), allow_nan=False, indent=2).encode('utf-8')
    last_exc = None
    for attempt in range(2):
        tmp_path = None
        try:
            with _omero_lock:
                conn = _ensure_omero_connection()
                img = conn.getObject('Image', int(image_id))
                if img is None:
                    raise ValueError("OMERO image %s not found" % image_id)
                old_ids = [a.getId() for a in img.listAnnotations(ns=ns)]
                fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='gater_')
                with os.fdopen(fd, 'wb') as fh:
                    fh.write(data)
                file_ann = conn.createFileAnnfromLocalFile(
                    tmp_path, origFilePathAndName=filename,
                    mimetype='application/json', ns=ns)
                img.linkAnnotation(file_ann)
                if old_ids:
                    conn.deleteObjects(
                        'Annotation', old_ids, deleteAnns=True, wait=True)
            return True
        except ValueError as exc:
            print("put_omero_json (%s): %s" % (kind, exc))
            return False
        except Exception as exc:  # noqa: BLE001 - connection loss etc.
            last_exc = exc
            with _omero_lock:
                try:
                    if _omero_conn is not None:
                        _omero_conn.close()
                except Exception:
                    pass
                _omero_conn = None
                _clear_omero_caches()
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
    print("put_omero_json (%s) failed after retry: %s" % (kind, last_exc))
    return False


def get_omero_json(image_id, kind):
    """Return the parsed JSON from the NEWEST Gater FileAnnotation of this
    namespace on the OMERO Image, or None if absent/unreadable. Best-effort:
    never raises (a None result triggers the caller's local fallback)."""
    global _omero_conn
    ns = _GATER_NS[kind]
    for attempt in range(2):
        try:
            with _omero_lock:
                conn = _ensure_omero_connection()
                img = conn.getObject('Image', int(image_id))
                if img is None:
                    return None
                best = None
                for a in img.listAnnotations(ns=ns):
                    if best is None or a.getId() > best.getId():
                        best = a
                if best is None:
                    return None
                raw = b''.join(best.getFileInChunks())
            return json.loads(raw.decode('utf-8'))
        except Exception as exc:  # noqa: BLE001 - connection loss etc.
            if attempt == 0:
                with _omero_lock:
                    try:
                        if _omero_conn is not None:
                            _omero_conn.close()
                    except Exception:
                        pass
                    _omero_conn = None
                    _clear_omero_caches()
                continue
            print("get_omero_json (%s): %s" % (kind, exc))
            return None
    return None


def get_omero_channel_info(image_id, name_prefix='channel'):
    """Return channel_info for an OMERO image for live-tile serving, mirroring
    the dict convertOmeTiff() returns for a local channel file — but sourced
    from OMERO metadata + OMERO's own resolution pyramid, so the (potentially
    huge) image is never downloaded.

    Keys: maxLevel, height, width, num_channels, tileHeight, tileWidth,
          channel_names (src identifiers, '<prefix>_<i>'),
          channel_labels (OMERO channel names for display/matching).
    """
    with _omero_lock:
        conn = _ensure_omero_connection()
        img = conn.getObject('Image', int(image_id))
        if img is None:
            raise ValueError("OMERO image %s not found" % image_id)
        num_c = img.getSizeC()
        h, w = img.getSizeY(), img.getSizeX()
        labels = []
        for i, ch in enumerate(img.getChannels()):
            labels.append(ch.getLabel() or ('ch%d' % i))
        rps = conn.c.sf.createRawPixelsStore()
        rps.setPixelsId(img.getPrimaryPixels().getId(), False)
        n_levels = len(rps.getResolutionDescriptions())
        rps.close()
    prefix = re.sub(r'[#\[\] ]+', '_', name_prefix)
    prefix = re.sub(r'_+', '_', prefix).strip('_') or 'channel'
    channel_names = ['%s_%d' % (prefix, i) for i in range(num_c)]
    return {
        'maxLevel': max(1, n_levels),
        'height': int(h),
        'width': int(w),
        'num_channels': int(num_c),
        'tileHeight': 1024,
        'tileWidth': 1024,
        'channel_names': channel_names,
        'channel_labels': labels,
    }


def init(datasource_name):
    load_ball_tree(datasource_name)


def load_datasource(datasource_name, reload=False):
    global datasource
    global source
    global config
    global seg
    global zarray
    global channels
    global metadata
    global _channel_axes
    if source is datasource_name and datasource is not None and reload is False:
        return
    with _load_lock:
        # Double-check after acquiring lock (another thread may have loaded)
        if source is datasource_name and datasource is not None and reload is False:
            return
        load_config(datasource_name)
        csvPath = Path(config[datasource_name]['featureData'][0]['src'])
        print("Loading csv data.. (this can take some time)")
        datasource = pd.read_csv(csvPath)
        datasource['id'] = datasource.index
        datasource = datasource.replace(-np.Inf, 0)

        # --- Load segmentation ---
        print("Loading segmentation.")
        if config[datasource_name]['segmentation'].endswith('.zarr'):
            seg = zarr.load(config[datasource_name]['segmentation'])
        else:
            seg_io = tf.TiffFile(config[datasource_name]['segmentation'], is_ome=False)
            seg = zarr.open(seg_io.series[0].aszarr())

        # --- Load channel image ---
        omero_image_id = config[datasource_name].get('omero_image_id')
        channel_file = config[datasource_name].get('channelFile', '')

        if omero_image_id and not channel_file:
            # Pure OMERO mode: no local channel file, load everything from OMERO.
            print(f"Loading channel data from OMERO image {omero_image_id}...")
            metadata = {}
            _channel_axes = 'CYX'
            channels = None  # tiles fetched on demand via _get_omero_tile()

            with _omero_lock:
                conn = _ensure_omero_connection()
                omero_image = conn.getObject('Image', omero_image_id)
                num_c = omero_image.getSizeC()
                cache = _omero_pixel_cache(omero_image_id)
                # Read the channel histogram / slider source from OMERO's
                # COARSEST pyramid level (small), so a gigapixel whole slide is
                # never fetched in one plane (which overflows OMERO's int32).
                n = len(cache['levels'])
                lw, lh = cache['levels'][n - 1]  # smallest level dims
                rps = conn.c.sf.createRawPixelsStore()
                rps.setPixelsId(cache['pid'], False)
                rps.setResolutionLevel(0)        # 0 = smallest
                planes = []
                for c in range(num_c):
                    buf = rps.getTile(0, c, 0, 0, 0, lw, lh)
                    plane = np.frombuffer(buf, dtype=cache['dtype']).reshape(lh, lw)
                    if plane.dtype == np.uint8:
                        plane = plane.astype(np.uint16) * np.uint16(257)
                    planes.append(plane)
                rps.close()
            zarray = np.stack(planes)  # CYX (coarse level)
            print(f"  OMERO histogram zarray: {zarray.shape}, dtype: {zarray.dtype}")
        else:
            # Local file mode (with or without OMERO tile serving).
            channel_io = tf.TiffFile(channel_file, is_ome=False)
            print("Loading image descriptions.")
            try:
                xml = channel_io.pages[0].tags['ImageDescription'].value
                metadata = from_xml(xml).images[0].pixels
            except:
                metadata = {}
            _channel_axes = channel_io.series[0].axes
            channels = zarr.open(channel_io.series[0].aszarr())

            if 'Y' in _channel_axes and 'X' in _channel_axes:
                _y_idx = _channel_axes.index('Y')
                _x_idx = _channel_axes.index('X')
            else:
                _y_idx, _x_idx = 1, 2

            level_series = next(
                (level for level in reversed(channel_io.series[0].levels)
                 if level.shape[_y_idx] >= 200 and level.shape[_x_idx] >= 200),
                channel_io.series[0]
            )
            zarray = zarr.open(level_series.aszarr())

            if 'S' in _channel_axes:
                zarray = np.transpose(np.asarray(zarray), (2, 0, 1))

            if hasattr(zarray, 'dtype') and zarray.dtype == np.uint8:
                zarray = np.asarray(zarray).astype(np.uint16) * np.uint16(257)

            if zarray.shape[1] > 400 or zarray.shape[2] > 400:
                x_reduce = zarray.shape[1] // 200
                y_reduce = zarray.shape[2] // 200
                reduce = np.min([x_reduce, y_reduce])
                zarray = block_reduce(zarray, (1, reduce, reduce), np.mean)

            if omero_image_id:
                print(f"  OMERO image {omero_image_id} will be used for tile serving.")

        # Set source LAST — after all globals are fully initialized.
        source = datasource_name
        if reload:
            # Rebuild the ball tree now that `source` is set. Doing this AFTER
            # source is assigned makes load_ball_tree's `datasource != source`
            # guard short-circuit instead of re-entering load_datasource, which
            # would deadlock on the non-reentrant _load_lock we still hold here.
            load_ball_tree(datasource_name, reload=reload)
        print("Data loading done.")


def load_config(datasource_name):
    global config

    with open(config_json_path, "r+") as configJson:
        config = json.load(configJson)
        updated = False
        # Update Feature SRC
        original = config[datasource_name]['featureData'][0]['src']
        config[datasource_name]['featureData'][0]['src'] = original.replace('static/data', 'minerva_analysis/data')
        csvPath = config[datasource_name]['featureData'][0]['src']
        if Path(csvPath).exists() is False:
            if Path('.' + csvPath).exists():
                csvPath = '.' + csvPath
        config[datasource_name]['featureData'][0]['src'] = str(Path(csvPath))
        if original != config[datasource_name]['featureData'][0]['src']:
            updated = True

        try:
            original = config[datasource_name]['segmentation']
            config[datasource_name]['segmentation'] = original.replace('static/data', 'minerva_analysis/data')
            if original != config[datasource_name]['segmentation']:
                updated = True

        except KeyError:
            print(datasource_name, 'is  missing segmentation')

        if updated:
            configJson.seek(0)  # <--- should reset file position to the beginning.
            json.dump(config, configJson, indent=4)
            configJson.truncate()


def load_ball_tree(datasource_name_name, reload=False):
    global ball_tree
    global datasource
    global config
    if datasource_name_name != source:
        load_datasource(datasource_name_name)

    # old with os.path
    # pickled_kd_tree_path = str(
    #     Path(
    #         os.path.join(os.getcwd())) / data_path / datasource_name_name / "ball_tree.pickle")

    #using pathlib now:
    pickled_kd_tree_path = str(
        PurePath(cwd_path, data_path, datasource_name_name, "ball_tree.pickle"))

    #old os.path way:  if os.path.isfile(pickled_kd_tree_path) and reload is False:
    if Path(pickled_kd_tree_path).is_file() and reload is False:

        print("Pickled KD Tree Exists, Loading")
        ball_tree = pickle.load(open(pickled_kd_tree_path, "rb"))
        print("Pickled KD Tree Loaded.")
    else:
        print("Creating KD Tree.")
        xCoordinate = config[datasource_name_name]['featureData'][0]['xCoordinate']
        yCoordinate = config[datasource_name_name]['featureData'][0]['yCoordinate']
        csvPath = Path(config[datasource_name_name]['featureData'][0]['src'])
        raw_data = pd.read_csv(csvPath)
        points = pd.DataFrame({'x': raw_data[xCoordinate], 'y': raw_data[yCoordinate]})
        ball_tree = BallTree(points, metric='euclidean')
        pickle.dump(ball_tree, open(pickled_kd_tree_path, 'wb'))
        print('Creating KD Tree done.')


def query_for_closest_cell(x, y, datasource_name):
    global datasource
    global source
    global ball_tree
    if datasource_name != source:
        load_ball_tree(datasource_name)
    distance, index = ball_tree.query([[x, y]], k=1)
    if distance == np.inf:
        return {}
    #         Nothing found
    else:
        try:
            row = datasource.iloc[index[0]]
            obj = row.to_dict(orient='records')[0]
            if 'celltype' not in obj:
                obj['celltype'] = ''
            return obj
        except:
            return {}


def get_row(row, datasource_name):
    global database
    global source
    global ball_tree
    if datasource_name != source:
        load_ball_tree(datasource_name)
    obj = database.loc[[row]].to_dict(orient='records')[0]
    obj['id'] = row
    return obj


def get_channel_names(datasource_name, shortnames=True):
    global datasource
    global source
    if datasource_name != source:
        load_ball_tree(datasource_name)
    if shortnames:
        channel_names = [channel['name'] for channel in config[datasource_name]['imageData'][1:]]
    else:
        channel_names = [channel['fullname'] for channel in config[datasource_name]['imageData'][1:]]
    return channel_names


def get_channel_cells(datasource_name, channels):
    global datasource
    global source
    global ball_tree

    range = [0, 65536]

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    query_string = ''
    for c in channels:
        if query_string != '':
            query_string += ' and '
        query_string += str(range[0]) + ' < `' + c + '` < ' + str(range[1])
    if query_string == None or query_string == "":
        return []
    query = datasource.query(query_string)[['id']].to_dict(orient='records')
    return query


def get_phenotype_description(datasource):
    try:
        data = ''
        csvPath = config[datasource]['featureData'][0]['celltypeData']
        if Path(csvPath).is_file():
        #old os.path usage: if os.path.isfile(csvPath):
            data = pd.read_csv(csvPath)
            data = data.to_numpy().tolist()
            # data = data.to_json(orient='records', lines=True)
        return data;
    except KeyError:
        return ''
    except TypeError:
        return ''


def get_phenotype_column_name(datasource):
    try:
        return config[datasource]['featureData'][0]['celltype']
    except KeyError:
        return ''
    except TypeError:
        return ''


def get_cells_phenotype(datasource_name):
    global datasource
    global source
    global ball_tree

    range = [0, 65536]

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    try:
        phenotype_field = config[datasource_name]['featureData'][0]['celltype']
    except KeyError:
        phenotype_field = 'celltype'
    except TypeError:
        phenotype_field = 'celltype'

    query = datasource[['id', phenotype_field]].to_dict(orient='records')
    return query


def get_phenotypes(datasource_name):
    global datasource
    global source
    global config
    try:
        phenotype_field = config[datasource_name]['featureData'][0]['celltype']
    except KeyError:
        phenotype_field = 'celltype'
    except TypeError:
        phenotype_field = 'celltype'

    if datasource_name != source:
        load_ball_tree(datasource_name)
    if phenotype_field in datasource.columns:
        return sorted(datasource[phenotype_field].unique().tolist())
    else:
        return ['']


def get_neighborhood(x, y, datasource_name, r=100, fields=None):
    global database
    global source
    global ball_tree
    if datasource_name != source:
        load_ball_tree(datasource_name)
    index = ball_tree.query_radius([[x, y]], r=r)
    neighbors = index[0]
    try:
        if fields and len(fields) > 0:
            fields.append('id') if 'id' not in fields else fields
            if len(fields) > 1:
                neighborhood = database.iloc[neighbors][fields].to_dict(orient='records')
            else:
                neighborhood = database.iloc[neighbors][fields].to_dict()
        else:
            neighborhood = database.iloc[neighbors].to_dict(orient='records')

        return neighborhood
    except:
        return {}


def get_number_of_cells_in_circle(x, y, datasource_name, r):
    global source
    global ball_tree
    if datasource_name != source:
        load_ball_tree(datasource_name)
    index = ball_tree.query_radius([[x, y]], r=r)
    try:
        return len(index[0])
    except:
        return 0


def get_color_scheme(datasource_name, refresh, label_field='celltype'):

    # old os.path way:
    # color_scheme_path = str(
    #     Path(os.path.join(os.getcwd())) / data_path / datasource_name / str(
    #         label_field + "_color_scheme.pickle"))

    color_scheme_path = str(PurePath(cwd_path, data_path, datasource_name, str(
            label_field + "_color_scheme.pickle")) )

    if refresh == False:
        #old os.path way:  if os.path.isfile(color_scheme_path):
        if Path(color_scheme_path).is_file():
            print("Color Scheme Exists, Loading")
            color_scheme = pickle.load(open(color_scheme_path, "rb"))
            return color_scheme
    if label_field == 'celltype':
        labels = get_phenotypes(datasource_name)
        print(labels)
    labels.append('SelectedCluster')
    color_scheme = {}
    colors = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf", "#808080", "#7A4900",
              "#0000A6", "#63FFAC", "#B79762", "#004D43", "#8FB0FF", "#997D87", "#5A0007", "#809693", "#FEFFE6",
              "#1B4400", "#4FC601", "#3B5DFF", "#4A3B53", "#FF2F80", "#61615A", "#BA0900", "#6B7900", "#00C2A0",
              "#FFAA92", "#FF90C9", "#B903AA", "#D16100", "#DDEFFF", "#000035", "#7B4F4B", "#A1C299", "#300018",
              "#0AA6D8", "#013349", "#00846F", "#372101", "#FFB500", "#C2FFED", "#A079BF", "#CC0744", "#C0B9B2",
              "#C2FF99", "#001E09", "#00489C", "#6F0062", "#0CBD66", "#EEC3FF", "#456D75", "#B77B68", "#7A87A1",
              "#788D66", "#885578", "#FAD09F", "#FF8A9A", "#D157A0", "#BEC459", "#456648", "#0086ED", "#886F4C",
              "#34362D", "#B4A8BD", "#00A6AA", "#452C2C", "#636375", "#A3C8C9", "#FF913F", "#938A81", "#575329",
              "#00FECF", "#B05B6F", "#8CD0FF", "#3B9700", "#04F757", "#C8A1A1", "#1E6E00", "#7900D7", "#A77500",
              "#6367A9", "#A05837", "#6B002C", "#772600", "#D790FF", "#9B9700", "#549E79", "#FFF69F", "#201625",
              "#72418F", "#BC23FF", "#99ADC0", "#3A2465", "#922329", "#5B4534", "#FDE8DC", "#404E55", "#0089A3",
              "#CB7E98", "#A4E804", "#324E72", "#6A3A4C", "#83AB58", "#001C1E", "#D1F7CE", "#004B28", "#C8D0F6",
              "#A3A489", "#806C66", "#222800", "#BF5650", "#E83000", "#66796D", "#DA007C", "#FF1A59", "#8ADBB4",
              "#1E0200", "#5B4E51", "#C895C5", "#320033", "#FF6832", "#66E1D3", "#CFCDAC", "#D0AC94", "#7ED379",
              "#012C58", "#7A7BFF", "#D68E01", "#353339", "#78AFA1", "#FEB2C6", "#75797C", "#837393", "#943A4D",
              "#B5F4FF", "#D2DCD5", "#9556BD", "#6A714A", "#001325", "#02525F", "#0AA3F7", "#E98176", "#DBD5DD",
              "#5EBCD1", "#3D4F44", "#7E6405", "#02684E", "#962B75", "#8D8546", "#9695C5", "#E773CE", "#D86A78",
              "#3E89BE", "#CA834E", "#518A87", "#5B113C", "#55813B", "#E704C4", "#00005F", "#A97399", "#4B8160",
              "#59738A", "#FF5DA7", "#F7C9BF", "#643127", "#513A01", "#6B94AA", "#51A058", "#A45B02", "#1D1702",
              "#E20027", "#E7AB63", "#4C6001", "#9C6966", "#64547B", "#97979E", "#006A66", "#391406", "#F4D749",
              "#0045D2", "#006C31", "#DDB6D0", "#7C6571", "#9FB2A4", "#00D891", "#15A08A", "#BC65E9", "#FFFFFE",
              "#C6DC99", "#203B3C", "#671190", "#6B3A64", "#F5E1FF", "#FFA0F2", "#CCAA35", "#374527", "#8BB400",
              "#797868", "#C6005A", "#3B000A", "#C86240", "#29607C", "#402334", "#7D5A44", "#CCB87C", "#B88183",
              "#AA5199", "#B5D6C3", "#A38469", "#9F94F0", "#A74571", "#B894A6", "#71BB8C", "#00B433", "#789EC9",
              "#6D80BA", "#953F00", "#5EFF03", "#E4FFFC", "#1BE177", "#BCB1E5", "#76912F", "#003109", "#0060CD",
              "#D20096", "#895563", "#29201D", "#5B3213", "#A76F42", "#89412E", "#1A3A2A", "#494B5A", "#A88C85",
              "#F4ABAA", "#A3F3AB", "#00C6C8", "#EA8B66", "#958A9F", "#BDC9D2", "#9FA064", "#BE4700", "#658188",
              "#83A485", "#453C23", "#47675D", "#3A3F00", "#061203", "#DFFB71", "#868E7E", "#98D058", "#6C8F7D",
              "#D7BFC2", "#3C3E6E", "#D83D66", "#2F5D9B", "#6C5E46", "#D25B88", "#5B656C", "#00B57F", "#545C46",
              "#866097", "#365D25", "#252F99", "#00CCFF", "#674E60", "#FC009C", "#92896B"]
    for i in range(len(labels)):
        color_scheme[str(labels[i])] = {}
        color_scheme[str(labels[i])]['rgb'] = list(ImageColor.getcolor(colors[i], "RGB"))
        color_scheme[str(labels[i])]['hex'] = colors[i]

    pickle.dump(color_scheme, open(color_scheme_path, 'wb'))
    return color_scheme


def get_rect_cells(datasource_name, rect, channels):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    # Query
    index = ball_tree.query_radius([[rect[0], rect[1]]], r=rect[2])
    print('Query size:', len(index[0]))
    neighbors = index[0]
    try:
        neighborhood = []
        for neighbor in neighbors:
            row = datasource.iloc[[neighbor]]
            obj = row.to_dict(orient='records')[0]
            if 'celltype' not in obj:
                obj['celltype'] = ''
            neighborhood.append(obj)
        return neighborhood
    except:
        return {}


def get_gated_cells(datasource_name, gates, start_keys):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    query_string = ''
    query_keys = start_keys
    for key, value in gates.items():
        if query_string != '':
            query_string += ' and '
        query_string += str(value[0]) + ' < `' + key + '` < ' + str(value[1])
        query_keys.append(key)
    if query_string is None or query_string == "":
        return []
    # query_keys[0] is the ID]
    query = datasource.query(query_string)[[query_keys[0]]].to_dict(orient='records')
    return query


def get_gated_cells_custom(datasource_name, gates, start_keys):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    # Query
    query_string = ''
    query_keys = start_keys
    for key, value in gates.items():
        if query_string != '':
            query_string += ' or '
        query_string += str(value[0]) + ' < `' + key + '` < ' + str(value[1])
        query_keys.append(key)
    if query_string is None or query_string == "":
        return []
    query = datasource.query(query_string)[query_keys].to_dict(orient='records')

    # TODO - likely lighter / less costly
    # query = database.query(query_string)[query_keys].to_dict('split')
    # del query['index']

    return query


def get_all_cells(datasource_name, start_keys, data_type=float):
    global datasource
    global source

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    query = datasource[start_keys].values.flatten('C');
    if np.issubdtype(data_type, int):
        return query.astype(np.uint32)
    return query.astype(np.float32)


def download_gating_csv(datasource_name, gates, channels, selection_ids, encoding):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    csv = datasource.copy()
    datasource_filter = datasource.copy()

    columns = []
    if 'idField' in config[datasource_name]['featureData'][0]:
        idField = config[datasource_name]['featureData'][0]['idField']
    else:
        idField = "CellID"
    columns.append(idField)

    if selection_ids:
        datasource_filter = datasource_filter[datasource_filter[idField].isin(selection_ids)]

    query_string = ''
    for key, value in gates.items():
        columns.append(key)
        if query_string != '':
            query_string += ' and '
        query_string += str(value[0]) + ' < `' + key + '` < ' + str(value[1])
    ids = datasource_filter.query(query_string)[['id']].to_numpy().flatten()

    if 'Area' in channels:
        del channels['Area']
    for channel in channels:
        if channel in gates:
            if encoding == 'binary':
                csv.loc[csv.index.isin(ids), channel] = 1
            csv.loc[~csv.index.isin(ids), channel] = 0
        else:
            csv[channel] = 0

    return csv


def download_gates(datasource_name, gates, channels, lassos):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)
    arr = []
    for key, value in channels.items():
        arr.append([key, value[0], value[1]])
    csv = pd.DataFrame(arr)
    csv.columns = ['channel', 'gate_start', 'gate_end']
    csv['gate_active'] = False
    for channel in gates:
        csv.loc[csv['channel'] == channel, 'gate_active'] = True
        csv.loc[csv['channel'] == channel, 'gate_start'] = gates[channel][0]
        csv.loc[csv['channel'] == channel, 'gate_end'] = gates[channel][1]

    if len(lassos) > 0:
        csv_count = 0
        for key, value in lassos.items():
            csv.loc[len(csv)+csv_count] = ['Lasso', value['lasso_polygon'], np.nan, value['lasso_toggle']]
            csv_count+=1

    return csv


def save_gating_list(datasource_name, gates, channels, lassos):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)
    arr = []
    for key, value in channels.items():
        arr.append([key, value[0], value[1]])
    csv = pd.DataFrame(arr)
    csv.columns = ['channel', 'gate_start', 'gate_end']
    csv['gate_active'] = False
    for channel in gates:
        csv.loc[csv['channel'] == channel, 'gate_active'] = True
        csv.loc[csv['channel'] == channel, 'gate_start'] = gates[channel][0]
        csv.loc[csv['channel'] == channel, 'gate_end'] = gates[channel][1]

    if len(lassos) > 0:
        csv_count = 0
        for key, value in lassos.items():
            csv.loc[len(csv)+csv_count] = ['Lasso', value['lasso_polygon'], np.nan, value['lasso_toggle']]
            csv_count+=1

    temp = csv.to_dict(orient='records')
    f = pickle.dumps(temp, protocol=4)
    database_model.save_list(database_model.GatingList, datasource=datasource_name, cells=f)
    # Mirror render state to OMERO (best-effort; local DB above is the fallback).
    image_id = _omero_image_id_for(datasource_name)
    if image_id is not None:
        put_omero_json(image_id, 'gating_list', temp)


def get_saved_gating_list(datasource_name):
    # OMERO primary: return the render state stored on the Image if present.
    image_id = _omero_image_id_for(datasource_name)
    if image_id is not None:
        remote = get_omero_json(image_id, 'gating_list')
        if remote is not None:
            return remote
    # Local fallback (unchanged: raises if nothing saved anywhere yet).
    gating_list = database_model.get(database_model.GatingList, datasource=datasource_name)
    local = pickle.loads(gating_list.cells)
    # Seed-on-load: copy the local state up to OMERO so it exists there next time.
    if image_id is not None:
        put_omero_json(image_id, 'gating_list', local)
    return local


def download_channels(datasource_name, map_channels, active_channels, list_colors, list_ranges, list_channels):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)
    arr = []
    for channel in map_channels:
        channel_name = map_channels[channel]
        arr.append([channel_name, list_channels[channel_name][0], list_channels[channel_name][1], 255, 255, 255, 1, False])
    csv = pd.DataFrame(arr)
    csv.columns = ['channel', 'start', 'end', 'r', 'g', 'b', 'opacity', 'channel_active']

    for channel in list_colors:
        csv.loc[csv['channel'] == map_channels[channel], 'r'] = list_colors[channel]['color']['r']
        csv.loc[csv['channel'] == map_channels[channel], 'g'] = list_colors[channel]['color']['g']
        csv.loc[csv['channel'] == map_channels[channel], 'b'] = list_colors[channel]['color']['b']
        csv.loc[csv['channel'] == map_channels[channel], 'opacity'] = list_colors[channel]['color']['opacity']
    for channel in active_channels:
        csv.loc[csv['channel'] == map_channels[channel], 'channel_active'] = True

    return csv


def save_channel_list(datasource_name, map_channels, active_channels, list_colors, list_ranges, list_channels):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)
    arr = []
    for channel in map_channels:
        channel_name = map_channels[channel]
        arr.append([channel_name, list_channels[channel_name][0], list_channels[channel_name][1], 255, 255, 255, 1, False])
    csv = pd.DataFrame(arr)
    csv.columns = ['channel', 'start', 'end', 'r', 'g', 'b', 'opacity', 'channel_active']

    for channel in list_colors:
        csv.loc[csv['channel'] == map_channels[channel], 'r'] = list_colors[channel]['color']['r']
        csv.loc[csv['channel'] == map_channels[channel], 'g'] = list_colors[channel]['color']['g']
        csv.loc[csv['channel'] == map_channels[channel], 'b'] = list_colors[channel]['color']['b']
        csv.loc[csv['channel'] == map_channels[channel], 'opacity'] = list_colors[channel]['color']['opacity']
    for channel in active_channels:
        csv.loc[csv['channel'] == map_channels[channel], 'channel_active'] = True

    temp = csv.to_dict(orient='records')
    f = pickle.dumps(temp, protocol=4)
    database_model.save_list(database_model.ChannelList, datasource=datasource_name, cells=f)
    # Mirror render state to OMERO (best-effort; local DB above is the fallback).
    image_id = _omero_image_id_for(datasource_name)
    if image_id is not None:
        put_omero_json(image_id, 'channel_list', temp)


def get_saved_channel_list(datasource_name):
    # OMERO primary: return the render state stored on the Image if present.
    image_id = _omero_image_id_for(datasource_name)
    if image_id is not None:
        remote = get_omero_json(image_id, 'channel_list')
        if remote is not None:
            return remote
    # Local fallback (unchanged: raises if nothing saved anywhere yet).
    channel_list = database_model.get(database_model.ChannelList, datasource=datasource_name)
    local = pickle.loads(channel_list.cells)
    # Seed-on-load: copy the local state up to OMERO so it exists there next time.
    if image_id is not None:
        put_omero_json(image_id, 'channel_list', local)
    return local


def get_datasource_description(datasource_name):
    global datasource
    global source
    global ball_tree
    global config

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)
    description = datasource.describe().to_dict()
    for column in description:
        column_data = datasource[column].to_numpy()
        [hist, bin_edges] = np.histogram(column_data[~np.isnan(column_data)], bins=50, density=True)
        midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2
        description[column]['histogram'] = {}
        dat = []
        for i in range(len(hist)):
            obj = {}
            obj['x'] = midpoints[i]
            obj['y'] = hist[i]
            dat.append(obj)
        description[column]['histogram'] = dat

    list_channels = config[datasource_name]['imageData']
    image_layer = 0
    for channel in list_channels:
        if channel['name'] != 'Area':
            fullName = channel['fullname']
            image_data = zarray[image_layer]
            image_layer += 1
            # Only channels that map to a CSV column get an image histogram.
            # An image channel whose name isn't a CSV column (e.g. an OMERO
            # channel label with no matching marker) is skipped, not crashed.
            if fullName not in description:
                continue
            img_log = np.log(image_data[image_data > 0])
            [hist, bin_edges] = np.histogram(img_log.flatten(), bins=50, density=True)
            midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2
            description[fullName]['image_histogram'] = {}

            dat = []
            for i in range(len(hist)):
                obj = {}
                obj['x'] = midpoints[i]
                obj['y'] = hist[i]
                dat.append(obj)

            description[fullName]['image_histogram'] = dat
            description[fullName]['image_min'] = np.ceil(np.exp(np.min(img_log)))
            description[fullName]['image_max'] = np.ceil(np.exp(np.max(img_log)))
        else:
            continue

    return description


def get_channel_gmm(channel_name, datasource_name):
    global datasource
    global source
    global ball_tree
    global config

    packet_gmm = {}

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)

    image_channelIdx = next(
        index for (index, d) in enumerate(config[datasource_name]['imageData']) if d["fullname"] == channel_name) - 1
    image_data = zarray[image_channelIdx]
    img_log = np.log(image_data[image_data > 0])
    gmm = GaussianMixture(3, max_iter=1000, tol=1e-6)
    gmm.fit(img_log.reshape((-1, 1)))

    means = gmm.means_[:, 0]
    i0, i1, i2 = np.argsort(means)
    mean1, mean2 = means[[i1, i2]]
    std1, std2 = gmm.covariances_[[i1, i2], 0, 0] ** 0.5

    x = np.linspace(mean1, mean2, 50)
    y1 = norm(mean1, std1).pdf(x) * gmm.weights_[i1]
    y2 = norm(mean2, std2).pdf(x) * gmm.weights_[i2]

    lmax = mean2 + 2 * std2
    lmin = x[np.argmin(np.abs(y1 - y2))]
    if lmin >= mean2:
        lmin = mean2 - 2 * std2
    vmin = max(np.exp(lmin), image_data.min(), 0)
    vmax = min(np.exp(lmax), image_data.max())

    packet_gmm['vmin'] = np.rint(vmin)
    packet_gmm['vmax'] = np.rint(vmax)

    [hist, bin_edges] = np.histogram(img_log.flatten(), bins=50, density=True)
    midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2

    covars = gmm.covariances_[:, 0, 0]
    weights = gmm.weights_
    pdf_gmm1 = weights[i0] * norm.pdf(midpoints, means[i0], np.sqrt(covars[i0]))
    pdf_gmm2 = weights[i1] * norm.pdf(midpoints, means[i1], np.sqrt(covars[i1]))
    pdf_gmm3 = weights[i2] * norm.pdf(midpoints, means[i2], np.sqrt(covars[i2]))

    dat_gmm1 = []
    dat_gmm2 = []
    dat_gmm3 = []
    for i in range(len(hist)):
        obj1 = {}
        obj1['x'] = midpoints[i]
        obj1['y'] = pdf_gmm1[i]
        dat_gmm1.append(obj1)

        obj2 = {}
        obj2['x'] = midpoints[i]
        obj2['y'] = pdf_gmm2[i]
        dat_gmm2.append(obj2)

        obj3 = {}
        obj3['x'] = midpoints[i]
        obj3['y'] = pdf_gmm3[i]
        dat_gmm3.append(obj3)

    packet_gmm['image_gmm_1'] = dat_gmm1
    packet_gmm['image_gmm_2'] = dat_gmm2
    packet_gmm['image_gmm_3'] = dat_gmm3

    return packet_gmm


def get_gating_gmm(channel_name, datasource_name, selection_ids):
    global datasource
    global source
    global ball_tree
    global config

    packet_gmm = {}

    # Load if not loaded
    if datasource_name != source:
        load_ball_tree(datasource_name)
    description = datasource.describe().to_dict()

    datasource_filter = datasource.copy()
    if 'idField' in config[datasource_name]['featureData'][0]:
        idField = config[datasource_name]['featureData'][0]['idField']
    else:
        idField = "CellID"
    if selection_ids:
        datasource_filter = datasource_filter[datasource_filter[idField].isin(selection_ids)]

    column_data = datasource[channel_name].to_numpy()
    [hist, bin_edges] = np.histogram(column_data[~np.isnan(column_data)], bins=50, density=True)
    midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2

    column_data_filtered = datasource_filter[channel_name].to_numpy()
    gmm = GaussianMixture(n_components=2)
    gmm.fit(column_data_filtered.reshape((-1, 1)))
    i0, i1 = np.argsort(gmm.means_[:, 0])
    packet_gmm['gate'] = np.mean(gmm.means_)

    pdf_gmm1 = [gmm.weights_[i0] * norm.pdf(midpoints, gmm.means_[i0], np.sqrt(gmm.covariances_[i0]))][0][0]
    pdf_gmm2 = [gmm.weights_[i1] * norm.pdf(midpoints, gmm.means_[i1], np.sqrt(gmm.covariances_[i1]))][0][0]

    dat_gmm1 = []
    dat_gmm2 = []
    for i in range(len(hist)):
        obj1 = {}
        obj1['x'] = midpoints[i]
        obj1['y'] = pdf_gmm1[i]
        dat_gmm1.append(obj1)

        obj2 = {}
        obj2['x'] = midpoints[i]
        obj2['y'] = pdf_gmm2[i]
        dat_gmm2.append(obj2)

    packet_gmm['gmm_1'] = dat_gmm1
    packet_gmm['gmm_2'] = dat_gmm2

    return packet_gmm


def generate_zarr_png(datasource_name, channel, level, tile):
    if config is None:
        load_datasource(datasource_name)
    global channels
    global seg
    global _channel_axes
    [tx, ty] = tile.replace('.png', '').split('_')
    tx = int(tx)
    ty = int(ty)
    level = int(level)
    tile_width = config[datasource_name]['tileWidth']
    tile_height = config[datasource_name]['tileHeight']
    ix = tx * tile_width
    iy = ty * tile_height
    segmentation = False
    try:
        channel_num = int(re.match(r".*_(\d*)$", channel).groups()[0])
    except AttributeError:
        segmentation = True
    if segmentation:
        # Clamp level to available segmentation pyramid levels
        if isinstance(seg, zarr.hierarchy.Group):
            level = min(level, len(seg) - 1)
        tile = seg[level][iy:iy + tile_height, ix:ix + tile_width]
        if tile.dtype.itemsize != 4:
            tile = tile.astype(np.uint32)
        tile = tile.view('uint8').reshape(tile.shape + (-1,))[..., [0, 1, 2]]
        tile = np.append(tile, np.zeros((tile.shape[0], tile.shape[1], 1), dtype='uint8'), axis=2)
    else:
        omero_image_id = config[datasource_name].get('omero_image_id')
        if omero_image_id:
            # OMERO path: fetch tile directly from server
            tile = _get_omero_tile(
                omero_image_id, channel_num, ix, iy, tile_width, tile_height,
                level=level,
            )
        elif isinstance(channels, zarr.Array):
            if 'S' in _channel_axes:
                tile = channels[iy:iy + tile_height, ix:ix + tile_width, channel_num]
            else:
                tile = channels[channel_num, iy:iy + tile_height, ix:ix + tile_width]
        else:
            # Clamp level to available channel pyramid levels
            clamped = min(level, len(channels) - 1)
            if 'S' in _channel_axes:
                tile = channels[clamped][iy:iy + tile_height, ix:ix + tile_width, channel_num]
            else:
                tile = channels[clamped][channel_num, iy:iy + tile_height, ix:ix + tile_width]
        # ViaWebGL only renders u16/u32 textures; ensure channel tiles are uint16.
        # Scale uint8 to full uint16 range (0-255 → 0-65535) so values are
        # visible against the client's hardcoded imageBitRange of [0, 65536].
        if tile.dtype == np.uint8:
            tile = tile.astype(np.uint16) * np.uint16(257)

    # tile = np.ascontiguousarray(tile, dtype='uint32')
    # png = tile.view('uint8').reshape(tile.shape + (-1,))[..., [2, 1, 0]]
    return tile


def get_ome_metadata(datasource_name):
    if config is None:
        load_datasource(datasource_name)
    global metadata
    return metadata


def _build_channel_pyramid(input_path, output_path, tile_size=1024):
    """Build SubIFD-based pyramidal OME-TIFF from a non-pyramidal channel image.

    Reads each channel lazily via zarr, downsamples with 2x2 block averaging,
    and writes all levels as SubIFDs.  Returns the total number of pyramid
    levels, or None if the image is too small to benefit from a pyramid.
    """
    tif_in = tf.TiffFile(str(input_path), is_ome=False)
    series = tif_in.series[0]
    axes = series.axes
    shape = series.shape
    dtype = series.dtype

    # Determine dimension layout
    is_interleaved = 'S' in axes
    if is_interleaved:
        c_dim = axes.index('S')
        y_dim = axes.index('Y')
        x_dim = axes.index('X')
    elif 'C' in axes:
        c_dim = axes.index('C')
        y_dim = axes.index('Y') if 'Y' in axes else 1
        x_dim = axes.index('X') if 'X' in axes else 2
    elif 'I' in axes:
        c_dim = axes.index('I')
        y_dim = axes.index('Y') if 'Y' in axes else 1
        x_dim = axes.index('X') if 'X' in axes else 2
    elif len(shape) == 2:
        c_dim = None
        y_dim, x_dim = 0, 1
    else:
        c_dim = 0
        y_dim, x_dim = 1, 2

    height, width = shape[y_dim], shape[x_dim]
    num_channels = shape[c_dim] if c_dim is not None else 1

    num_levels = int(np.ceil(np.log2(max(height, width) / tile_size))) + 1
    if num_levels < 2:
        tif_in.close()
        return None

    num_sub = num_levels - 1
    src = zarr.open(series.aszarr(), mode='r')

    print(f"Building channel pyramid: {num_channels}ch, {num_levels} levels, "
          f"{width}x{height} {dtype}")

    with tf.TiffWriter(str(output_path), bigtiff=True, ome=True) as tw:
        for c in range(num_channels):
            print(f"  Channel {c + 1}/{num_channels}...")
            # Extract 2D channel data
            if c_dim is None:
                ch = np.asarray(src[:])
            elif is_interleaved:
                ch = np.asarray(src[:, :, c])
            else:
                ch = np.asarray(src[c])

            # Level 0 — full resolution
            tw.write(
                ch,
                tile=(tile_size, tile_size),
                subifds=num_sub,
                compression='zlib',
            )

            # Downsampled sub-levels
            prev = ch
            for _ in range(num_sub):
                down = block_reduce(prev, (2, 2), np.mean)
                if down.dtype != dtype:
                    down = down.astype(dtype)
                tw.write(
                    down,
                    tile=(tile_size, tile_size),
                    subfiletype=1,
                    compression='zlib',
                )
                prev = down
            del ch

    tif_in.close()
    print(f"Channel pyramid written: {output_path} ({num_levels} levels)")
    return num_levels


def convertOmeTiff(filePath, channelFilePath=None, dataDirectory=None, isLabelImg=False):
    channel_info = {}
    channelNames = []

    # image is a normal channel?
    if isLabelImg == False:
        channel_io = tf.TiffFile(str(filePath), is_ome=False)
        channels = zarr.open(channel_io.series[0].aszarr())
        if isinstance(channels, zarr.Array):
            channel_info['maxLevel'] = 1
            chunks = channels.chunks
            shape = channels.shape
        else:
            channel_info['maxLevel'] = len(channels)
            shape = channels[0].shape
            chunks = (1, 1024, 1024)

        # Determine dimension indices from tifffile axes metadata.
        # Handles CYX/IYX (channel-first) and YXS (interleaved RGB).
        axes = channel_io.series[0].axes
        if 'S' in axes:
            c_dim = axes.index('S')
        elif 'C' in axes:
            c_dim = axes.index('C')
        elif 'I' in axes:
            c_dim = axes.index('I')
        elif len(shape) == 2:
            c_dim = None
        else:
            c_dim = 0
        y_dim = axes.index('Y') if 'Y' in axes else (0 if c_dim != 0 else 1)
        x_dim = axes.index('X') if 'X' in axes else (1 if c_dim != 1 else 2)

        num_channels = shape[c_dim] if c_dim is not None else 1
        # Cap tile sizes to 1024 — non-tiled images have chunks equal to full
        # image dimensions, which would create single-tile requests too large
        # for the browser.
        raw_th = chunks[y_dim] if len(chunks) > y_dim else 1024
        raw_tw = chunks[x_dim] if len(chunks) > x_dim else 1024
        channel_info['tileHeight'] = min(raw_th, 1024)
        channel_info['tileWidth'] = min(raw_tw, 1024)
        channel_info['height'] = shape[y_dim]
        channel_info['width'] = shape[x_dim]
        channel_info['num_channels'] = num_channels
        for i in range(num_channels):
            stem = re.sub(r'\.ome\.tiff|\.ome\.tif|\.tiff|\.tif|\.png|\.svs|\.ndpi|\.qptiff', '', filePath.name)
            # Sanitize characters that break HTTP URLs (# is fragment separator,
            # [] and spaces cause routing issues in tile request paths).
            stem = re.sub(r'[#\[\] ]+', '_', stem)
            stem = re.sub(r'_+', '_', stem).strip('_')
            channelName = stem + "_" + str(i)
            channelNames.append(channelName)
        channel_info['channel_names'] = channelNames

        # Build pyramid for non-pyramidal channel images so the client can
        # request lower-resolution tiles at zoomed-out view levels.
        if channel_info['maxLevel'] == 1 and dataDirectory is not None:
            if max(channel_info['height'], channel_info['width']) > 1024:
                pyr_name = re.sub(
                    r'\.ome\.tiff$|\.ome\.tif$|\.tiff$|\.tif$', '',
                    filePath.name
                ) + '_pyramid.ome.tif'
                pyr_path = Path(dataDirectory) / pyr_name
                if not pyr_path.exists():
                    num_levels = _build_channel_pyramid(filePath, pyr_path)
                else:
                    pyr_io = tf.TiffFile(str(pyr_path), is_ome=False)
                    pyr_z = zarr.open(pyr_io.series[0].aszarr())
                    num_levels = (len(pyr_z)
                                  if isinstance(pyr_z, zarr.hierarchy.Group)
                                  else 1)
                    pyr_io.close()
                if num_levels and num_levels > 1:
                    channel_info['maxLevel'] = num_levels
                    channel_info['channelFile'] = str(pyr_path)
                    channel_info['tileHeight'] = 1024
                    channel_info['tileWidth'] = 1024

        return channel_info

    # segmentation mask
    else:
        channel_io = tf.TiffFile(str(channelFilePath), is_ome=False)
        channels = zarr.open(channel_io.series[0].aszarr())
        write_path = None

        # Handle .zarr segmentation masks (OMERO OME-NGFF)
        if str(filePath).endswith('.zarr'):
            zarr_stem = Path(filePath).stem
            directory = Path(dataDirectory) / (zarr_stem + '.ome.tif')
            if not directory.exists():
                omero_zarr_to_ometiff(Path(filePath), output_path=directory)
            write_path = str(directory)
        else:
            # Existing TIFF path
            directory = Path(dataDirectory + "/" + filePath.name)
            segmentation_mask = tf.TiffFile(str(filePath), is_ome=False)
            if segmentation_mask.series[0].aszarr().is_multiscales is False:
                args = {}
                args['in_paths'] = [Path(filePath)]
                args['out_path'] = directory
                args['is_mask'] = True
                pyramid_assemble.main(py_args=args)
                pyramid_upgrade.main(py_args=args)
                write_path = str(directory)
            else:
                write_path = str(filePath)
        # Count segmentation pyramid levels so config can reflect them.
        seg_levels = 1
        if write_path:
            seg_io = tf.TiffFile(write_path, is_ome=False)
            seg_z = zarr.open(seg_io.series[0].aszarr())
            if isinstance(seg_z, zarr.hierarchy.Group):
                seg_levels = len(seg_z)
            seg_io.close()
        return {'segmentation': write_path, 'maxLevel': seg_levels}


def logTransform(csvPath, skip_columns=[]):
    df = pd.read_csv(csvPath)
    for column in df.columns:
        if column not in skip_columns:
            df[column] = np.log1p(df[column])
    df.to_csv(csvPath, index=False)

# similar_neighborhood=False, embedding=False
def get_cells_in_polygon(datasource_name, points):
    global config
    global datasource
    global ball_tree

    if datasource_name != source:
        load_datasource(datasource_name)

    point_tuples = [(e['imagePoints']['x'], e['imagePoints']['y']) for e in points]
    (x, y, r) = smallestenclosingcircle.make_circle(point_tuples)

    index = ball_tree.query_radius([[x, y]], r)
    neighbors = index[0]
    circle_neighbors = datasource.iloc[neighbors].to_dict(orient='records')
    neighbor_points = pd.DataFrame(circle_neighbors).values

    path = mpltPath.Path(point_tuples)
    inside = path.contains_points(neighbor_points[:, [1, 2]].astype('float'))
    neighbor_ids = neighbor_points[np.where(inside == True), 0].astype('int').flatten().tolist()
    neighbor_ids.sort()

    packet = neighbor_ids
    return packet

def get_cells_in_lassos(datasource_name, list_lassos):
    global config
    global datasource
    global ball_tree

    if datasource_name != source:
        load_datasource(datasource_name)

    list_lassos_active = {k: v for k, v in list_lassos.items() if v.get('lasso_toggle') == True}

    list_ids = []
    list_ids_subtract = []
    for v in list_lassos_active.values():
        list_ids.extend(v.get('lasso_ids', []))
        list_ids_subtract.extend(v.get('lasso_ids_subtract', []))
    list_ids = list(set(list_ids))
    list_ids.sort()

    if 'idField' in config[datasource_name]['featureData'][0]:
        idField = config[datasource_name]['featureData'][0]['idField']
    else:
        idField = "CellID"
    list_ids_subtract = list(set(datasource[idField]) - set(list_ids))
    list_ids_subtract.sort()

    packet = {'lasso_ids': list_ids, 'lasso_ids_subtract': list_ids_subtract}
    return packet
