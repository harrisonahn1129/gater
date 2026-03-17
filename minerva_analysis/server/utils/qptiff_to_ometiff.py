#!/usr/bin/env python3
"""Convert QPTIFF to OME-TIFF, preserving original bit depth (dtype).

Writes BigTIFF with tiled output for large images. Uses a disk-backed
numpy memmap to assemble the CYX array channel-by-channel, avoiding
loading the entire image into RAM at once.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import zarr
from tifffile import TiffFile, TiffWriter


def get_channel_names(path):
    """Try to get channel names via qptifffile if available."""
    try:
        from qptifffile import QPTiffFile
        with QPTiffFile(path) as qp:
            names = qp.get_biomarkers()
            if names:
                return list(names)
    except Exception:
        pass
    return None


def _get_cyx_info(shape, axes):
    """Determine (num_channels, height, width, is_yxs) from shape and axes string."""
    if len(shape) == 2:
        return 1, shape[0], shape[1], False
    if axes:
        if 'S' in axes:
            c_idx = axes.index('S')
            y_idx = axes.index('Y') if 'Y' in axes else 0
            x_idx = axes.index('X') if 'X' in axes else 1
            return shape[c_idx], shape[y_idx], shape[x_idx], True
        if 'C' in axes:
            c_idx = axes.index('C')
            y_idx = axes.index('Y') if 'Y' in axes else 1
            x_idx = axes.index('X') if 'X' in axes else 2
            return shape[c_idx], shape[y_idx], shape[x_idx], False
        if 'I' in axes:
            c_idx = axes.index('I')
            y_idx = axes.index('Y') if 'Y' in axes else 1
            x_idx = axes.index('X') if 'X' in axes else 2
            return shape[c_idx], shape[y_idx], shape[x_idx], False
    # Guess from shape: if last dim is smallest, it's interleaved (YXS)
    if len(shape) == 3:
        if shape[2] < shape[0] and shape[2] < shape[1]:
            return shape[2], shape[0], shape[1], True
        return shape[0], shape[1], shape[2], False
    return shape[0], shape[1], shape[2], False


def qptiff_to_ometiff(input_path, output_path=None, channel_names=None, physical_size_xy=None):
    """Convert QPTIFF to OME-TIFF, preserving bit depth.

    Uses a disk-backed memmap to assemble the CYX array without loading
    the entire image into RAM. Writes BigTIFF with 1024x1024 tiles.

    Returns output Path.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Not found: {input_path}")

    output_path = Path(output_path) if output_path else input_path.with_suffix(".ome.tif")

    with TiffFile(input_path) as tif:
        s = tif.series[0]
        shape = s.shape
        dtype = s.dtype
        axes = getattr(s, 'axes', '')

        num_channels, height, width, is_yxs = _get_cyx_info(shape, axes)

        # Open zarr store for lazy channel-by-channel reading
        store = s.aszarr()
        zdata = zarr.open(store, mode='r')

        # zarr store may be a Group (multi-level pyramid) or an Array
        if isinstance(zdata, zarr.hierarchy.Group):
            zdata = zdata['0']  # level 0 = full resolution

        print(
            f"Converting QPTIFF -> OME-TIFF: {num_channels} channels, "
            f"{height}x{width}, {dtype}\n  {input_path} -> {output_path}",
            file=sys.stderr,
        )

        # Use a disk-backed memmap to build the CYX array without loading
        # everything into RAM (e.g. 17 GB for 8-channel, 43 GB for 28-channel).
        # tifffile reads tiles from the memmap on demand during write.
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.dat')
        os.close(tmp_fd)
        try:
            mmap = np.memmap(
                tmp_path, dtype=dtype, mode='w+',
                shape=(num_channels, height, width),
            )

            for c in range(num_channels):
                if len(shape) == 2:
                    mmap[0] = np.asarray(zdata)
                elif is_yxs:
                    mmap[c] = np.asarray(zdata[:, :, c])
                else:
                    mmap[c] = np.asarray(zdata[c])
                mmap.flush()
                print(f"  Channel {c+1}/{num_channels} read", file=sys.stderr)

            store.close()

            # Write as a single CYX tiled BigTIFF.
            # Channel names are generated from the filename by convertOmeTiff(),
            # so OME XML channel metadata is not needed here.
            with TiffWriter(output_path, bigtiff=True) as writer:
                writer.write(
                    mmap,
                    tile=(1024, 1024),
                    photometric='minisblack',
                    metadata={'axes': 'CYX'},
                )

            print(f"  Written: {output_path}", file=sys.stderr)
        finally:
            del mmap
            os.unlink(tmp_path)

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Convert QPTIFF to OME-TIFF, preserve bit depth.")
    parser.add_argument("input", type=Path, help="Input .qptiff path")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output .ome.tif path")
    parser.add_argument("--physical-size", type=float, nargs=2, metavar=("X", "Y"), help="Pixel size (µm)")
    args = parser.parse_args()

    try:
        out = qptiff_to_ometiff(args.input, output_path=args.output, physical_size_xy=args.physical_size)
        print(f"Written: {out}", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
