#!/usr/bin/env python3
"""Convert OMERO OME-NGFF (.zarr) segmentation masks to pyramidal OME-TIFF.

OMERO stores segmentation masks as OME-NGFF zarr directories with 5D arrays
[T,C,Z,Y,X] (uint32) and pre-computed resolution pyramids. This module
converts them to SubIFD-based pyramidal OME-TIFF files that the gater app
can read via tifffile's zarr-backed interface.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import zarr
from tifffile import TiffWriter


def discover_label_group(zarr_path):
    """Navigate the zarr hierarchy to find the label group and pyramid levels.

    OMERO OME-NGFF layout: ``<root>/0/labels/<label_name>/``
    Each level directory (0, 1, 2, ...) contains a 5D array [T,C,Z,Y,X].

    Returns:
        (label_group, level_paths): The zarr group for the label and an
        ordered list of resolution level paths (highest-res first).

    Raises:
        FileNotFoundError: If no labels are found.
    """
    zarr_path = Path(zarr_path)
    root = zarr.open(str(zarr_path), mode='r')

    # Navigate to labels group: 0/labels/
    labels_group = root['0']['labels']

    # Discover label name dynamically (first entry)
    label_names = list(labels_group.keys())
    if not label_names:
        raise FileNotFoundError(f"No labels found in {zarr_path}/0/labels/")
    label_name = label_names[0]
    label_group = labels_group[label_name]

    # Read multiscales metadata for ordered level paths
    zattrs_path = zarr_path / '0' / 'labels' / label_name / '.zattrs'
    if zattrs_path.exists():
        with open(zattrs_path) as f:
            zattrs = json.load(f)
        multiscales = zattrs.get('multiscales', [{}])[0]
        datasets = multiscales.get('datasets', [])
        level_paths = [d['path'] for d in datasets]
    else:
        # Fallback: sort numeric directory names
        level_paths = sorted(
            [k for k in label_group.keys() if k.isdigit()],
            key=int
        )

    if not level_paths:
        raise FileNotFoundError(
            f"No resolution levels found in {zarr_path}/0/labels/{label_name}/"
        )

    print(
        f"Found label '{label_name}' with {len(level_paths)} levels",
        file=sys.stderr,
    )
    return label_group, level_paths


def _zarr_tile_iter(zarr_array, tile_h, tile_w):
    """Memory-efficient tile iterator for a 5D zarr array.

    Reads tile_h x tile_w chunks from the zarr array, squeezes the leading
    dimensions (T,C,Z) to produce 2D tiles, and yields them in row-major
    order — the order tifffile expects for tiled writes.

    Each tile is ~1 MB (512x512 uint32), so only one tile is in memory at
    a time regardless of the full array size.
    """
    shape = zarr_array.shape
    # 5D: [T, C, Z, Y, X]
    if len(shape) == 5:
        height, width = shape[3], shape[4]
    elif len(shape) == 2:
        height, width = shape
    else:
        height, width = shape[-2], shape[-1]

    for y in range(0, height, tile_h):
        for x in range(0, width, tile_w):
            y_end = min(y + tile_h, height)
            x_end = min(x + tile_w, width)

            if len(shape) == 5:
                tile = zarr_array[0, 0, 0, y:y_end, x:x_end]
            elif len(shape) == 2:
                tile = zarr_array[y:y_end, x:x_end]
            else:
                # Generic: take last two dims
                slices = (0,) * (len(shape) - 2) + (slice(y, y_end), slice(x, x_end))
                tile = zarr_array[slices]

            tile = np.ascontiguousarray(np.squeeze(tile))
            yield tile


def omero_zarr_to_ometiff(zarr_path, output_path=None, tile_size=512):
    """Convert an OMERO OME-NGFF zarr segmentation mask to pyramidal OME-TIFF.

    Writes all pre-computed zarr pyramid levels directly to a SubIFD-based
    pyramidal OME-TIFF. No recomputation of the pyramid is needed.

    Args:
        zarr_path: Path to the .zarr directory.
        output_path: Optional output path. Defaults to <zarr_stem>.ome.tif
            next to the zarr directory.
        tile_size: Tile size for the output TIFF (default 512).

    Returns:
        Path to the written OME-TIFF file.
    """
    zarr_path = Path(zarr_path)
    if not zarr_path.exists():
        raise FileNotFoundError(f"Not found: {zarr_path}")

    if output_path is None:
        output_path = zarr_path.with_suffix('.ome.tif')
    else:
        output_path = Path(output_path)

    label_group, level_paths = discover_label_group(zarr_path)

    # Open all levels and get their shapes
    levels = []
    for lp in level_paths:
        arr = label_group[lp]
        levels.append(arr)

    level0 = levels[0]
    shape0 = level0.shape
    if len(shape0) == 5:
        height0, width0 = shape0[3], shape0[4]
    elif len(shape0) == 2:
        height0, width0 = shape0
    else:
        height0, width0 = shape0[-2], shape0[-1]

    dtype = level0.dtype
    num_sub_levels = len(levels) - 1

    print(
        f"Converting: {zarr_path.name} -> {output_path.name}\n"
        f"  Level 0: {width0}x{height0}, dtype={dtype}, "
        f"{num_sub_levels} sub-levels",
        file=sys.stderr,
    )

    metadata = {
        'axes': 'YX',
    }

    tile = (tile_size, tile_size)

    with TiffWriter(str(output_path), bigtiff=True, ome=True) as tw:
        # Level 0 — full resolution, declares SubIFD slots
        tw.write(
            _zarr_tile_iter(level0, tile_size, tile_size),
            shape=(height0, width0),
            dtype=dtype,
            tile=tile,
            subifds=num_sub_levels if num_sub_levels > 0 else None,
            compression='zlib',
            metadata=metadata,
        )

        # Levels 1..N — fill SubIFD slots
        for i, level_arr in enumerate(levels[1:], start=1):
            level_shape = level_arr.shape
            if len(level_shape) == 5:
                lh, lw = level_shape[3], level_shape[4]
            elif len(level_shape) == 2:
                lh, lw = level_shape
            else:
                lh, lw = level_shape[-2], level_shape[-1]

            print(
                f"  Level {i}: {lw}x{lh}",
                file=sys.stderr,
            )

            tw.write(
                _zarr_tile_iter(level_arr, tile_size, tile_size),
                shape=(lh, lw),
                dtype=dtype,
                tile=tile,
                subfiletype=1,
                compression='zlib',
            )

    print(f"Written: {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Convert OMERO OME-NGFF (.zarr) segmentation masks to pyramidal OME-TIFF."
    )
    parser.add_argument("input", type=Path, help="Input .zarr directory path")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="Output .ome.tif path"
    )
    parser.add_argument(
        "--tile-size", type=int, default=512, help="Tile size (default: 512)"
    )
    args = parser.parse_args()

    try:
        omero_zarr_to_ometiff(
            args.input,
            output_path=args.output,
            tile_size=args.tile_size,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
