#!/usr/bin/env python3
"""Convert TIFF-based whole slide images (SVS, NDPI) to OME-TIFF."""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
from tifffile import TiffFile, TiffWriter

SUPPORTED_EXTENSIONS = {".svs", ".ndpi"}


def read_wsi_metadata(path):
    """Extract metadata from a whole slide image (SVS or NDPI).

    Parses dimensions, dtype, and pixel size from format-specific metadata.
    For SVS: reads MPP (microns per pixel) from the Aperio ImageDescription.
    For NDPI: converts XResolution/YResolution (pixels/cm) to microns/pixel.

    Returns dict: height, width, channels, dtype, physical_size_x/y, format_name.
    """
    path = Path(path)
    ext = path.suffix.lower()

    with TiffFile(path) as tif:
        s = tif.series[0]
        shape = s.shape
        dtype = s.dtype
        axes = s.axes.upper()

        # Determine spatial dimensions and channel count from the axes string
        if axes == "YXS":
            height, width, channels = shape
        elif axes == "SYX":
            channels, height, width = shape
        elif axes == "YX":
            height, width = shape
            channels = 1
        elif axes == "CYX":
            channels, height, width = shape
        else:
            # Fallback: assume last two dims are Y, X
            height, width = shape[-2], shape[-1]
            channels = shape[0] if len(shape) == 3 else 1

        physical_size_x = None
        physical_size_y = None
        page0 = tif.pages[0]

        if ext == ".svs":
            # SVS stores pixel size as MPP in the Aperio ImageDescription tag
            physical_size_x, physical_size_y = _parse_svs_pixel_size(page0)

        elif ext == ".ndpi":
            # NDPI stores resolution as pixels per centimeter in TIFF tags
            physical_size_x, physical_size_y = _parse_resolution_tags(page0)

        # Fallback: try resolution tags for any format if not yet parsed
        if physical_size_x is None:
            physical_size_x, physical_size_y = _parse_resolution_tags(page0)

        # Symmetry fallback: if only one axis was found, use it for both
        if physical_size_y is None:
            physical_size_y = physical_size_x
        if physical_size_x is None:
            physical_size_x = physical_size_y

        format_name = {".svs": "Aperio SVS", ".ndpi": "Hamamatsu NDPI"}.get(ext, "WSI")

        return {
            "height": height,
            "width": width,
            "channels": channels,
            "dtype": dtype,
            "physical_size_x": physical_size_x,
            "physical_size_y": physical_size_y,
            "format_name": format_name,
        }


def _parse_svs_pixel_size(page):
    """Parse microns-per-pixel from the Aperio ImageDescription tag.

    Aperio SVS format stores metadata as pipe-delimited key=value pairs,
    e.g. '...|MPP = 0.2500|AppMag = 20|...'. Returns (size_x, size_y) in µm.
    """
    if "ImageDescription" not in page.tags:
        return None, None

    desc = page.tags["ImageDescription"].value
    if isinstance(desc, bytes):
        desc = desc.decode("utf-8", errors="ignore")

    mpp_match = re.search(r"MPP\s*=\s*([\d.]+)", desc)
    if mpp_match:
        mpp = float(mpp_match.group(1))
        return mpp, mpp

    return None, None


def _parse_resolution_tags(page):
    """Parse pixel size from TIFF XResolution/YResolution tags.

    Handles ResolutionUnit 2 (inch) and 3 (centimeter).
    Returns (size_x, size_y) in µm, or (None, None) if tags are missing.
    """
    # Determine the conversion factor based on ResolutionUnit
    unit = 2  # TIFF default is inches
    if "ResolutionUnit" in page.tags:
        unit = page.tags["ResolutionUnit"].value

    # Microns per unit: 25400 µm/inch or 10000 µm/cm
    if unit == 3:
        um_per_unit = 10000.0
    elif unit == 2:
        um_per_unit = 25400.0
    else:
        # Unit 1 means "no absolute unit"; can't convert to physical size
        return None, None

    size_x = _resolution_tag_to_um(page, "XResolution", um_per_unit)
    size_y = _resolution_tag_to_um(page, "YResolution", um_per_unit)
    return size_x, size_y


def _resolution_tag_to_um(page, tag_name, um_per_unit):
    """Convert a single resolution tag value to microns per pixel.

    The tag value is pixels-per-unit (rational or float).
    Microns per pixel = um_per_unit / pixels_per_unit.
    """
    if tag_name not in page.tags:
        return None

    val = page.tags[tag_name].value
    # tifffile returns rationals as tuples (numerator, denominator)
    if isinstance(val, tuple) and len(val) == 2:
        if val[1] == 0:
            return None
        pixels_per_unit = val[0] / val[1]
    else:
        pixels_per_unit = float(val)

    if pixels_per_unit <= 0:
        return None
    return um_per_unit / pixels_per_unit


def _cyx_tile_iter(data, channels, height, width, tile_h, tile_w):
    """Yield (tile_h, tile_w) tiles from a YXS array in CYX order.

    Iterates channel-by-channel, row-by-row, column-by-column — the tile
    order tifffile expects for a (C, Y, X) tiled write.  Each tile is a
    small contiguous copy (~256 KB at 512x512 uint8), so the full-array
    CYX transpose (which would copy 13-15 GB) is never performed.
    """
    for c in range(channels):
        for y in range(0, height, tile_h):
            for x in range(0, width, tile_w):
                tile = data[y : min(y + tile_h, height), x : min(x + tile_w, width), c]
                yield np.ascontiguousarray(tile)


def to_cyx(data):
    """Normalize array to CYX (channels, Y, X).

    Handles common WSI layouts: YX (grayscale), YXS (RGB interleaved),
    and higher-dimensional arrays from multi-resolution series.
    Used as a fallback when the data is not in YXS layout and the tile
    iterator cannot be applied.
    """
    if data.ndim == 2:
        # Grayscale YX -> add channel dimension
        return data[np.newaxis, ...]
    if data.ndim == 3:
        # YXS (interleaved RGB): last dim is smallest -> move to front
        if data.shape[2] < data.shape[0] and data.shape[2] < data.shape[1]:
            return np.moveaxis(data, 2, 0)
        return data
    if data.ndim == 4:
        return data[0] if data.shape[0] <= 4 else data[:, 0]
    if data.ndim == 5:
        return data[0, 0]
    return data


def wsi_to_ometiff(input_path, output_path=None, tile_size=512, physical_size_xy=None):
    """Convert a whole slide image (SVS or NDPI) to OME-TIFF.

    Reads the full-resolution image from series[0] and writes a tiled
    CYX OME-TIFF.  For the common YXS (interleaved RGB) layout, a tile
    iterator transposes small tiles on the fly — avoiding a full-array
    contiguous copy that would otherwise double RAM usage and add ~30 s.

    Args:
        input_path: Path to the input SVS or NDPI file.
        output_path: Optional output path. Defaults to input stem + .ome.tif.
        tile_size: Tile size for the output OME-TIFF (default 512).
        physical_size_xy: Optional (x, y) pixel size in µm to override metadata.

    Returns:
        Path to the written OME-TIFF file.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Not found: {input_path}")

    ext = input_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported format '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    output_path = Path(output_path) if output_path else input_path.with_suffix(".ome.tif")

    # Read metadata for pixel size and format info
    meta = read_wsi_metadata(input_path)
    print(
        f"Input: {meta['format_name']}, {meta['width']}x{meta['height']}, "
        f"{meta['channels']}ch, {meta['dtype']}",
        file=sys.stderr,
    )

    # Read the full-resolution image from the first series
    with TiffFile(input_path) as tif:
        data = tif.series[0].asarray()

    # Determine pixel size: CLI override > file metadata > fallback to 1.0
    if physical_size_xy:
        px, py = physical_size_xy
    else:
        px = meta["physical_size_x"]
        py = meta["physical_size_y"]
    if px is None or py is None:
        px = py = 1.0

    # Assign channel names: R/G/B for 3-channel, generic otherwise
    nch = meta["channels"]
    if nch == 3:
        channel_names = ["Red", "Green", "Blue"]
    else:
        channel_names = [f"Channel_{i}" for i in range(nch)]

    # Build OME metadata dict consumed by tifffile
    ome_metadata = {
        "axes": "CYX",
        "PhysicalSizeX": px,
        "PhysicalSizeXUnit": "µm",
        "PhysicalSizeY": py,
        "PhysicalSizeYUnit": "µm",
        "Channel": {"Name": channel_names},
    }

    H, W = meta["height"], meta["width"]

    # Write tiled OME-TIFF with bigtiff=True (output may exceed 4 GB).
    # For YXS data (the common case for SVS/NDPI), use a tile iterator to
    # avoid the costly full-array CYX transpose.  For other layouts, fall
    # back to materializing the full CYX array.
    is_yxs = data.ndim == 3 and data.shape == (H, W, nch)

    with TiffWriter(output_path, bigtiff=True) as tw:
        if is_yxs:
            tw.write(
                _cyx_tile_iter(data, nch, H, W, tile_size, tile_size),
                shape=(nch, H, W),
                dtype=data.dtype,
                tile=(tile_size, tile_size),
                resolution=(1.0 / py, 1.0 / px),
                metadata=ome_metadata,
            )
        else:
            arr = to_cyx(data)
            if not arr.flags.c_contiguous:
                arr = np.ascontiguousarray(arr)
            tw.write(
                arr,
                tile=(tile_size, tile_size),
                resolution=(1.0 / py, 1.0 / px),
                metadata=ome_metadata,
            )

    print(f"Written: {output_path} ({nch}, {H}, {W})", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Convert whole slide images (SVS, NDPI) to OME-TIFF."
    )
    parser.add_argument("input", type=Path, help="Input .svs or .ndpi path")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output .ome.tif path")
    parser.add_argument("--tile-size", type=int, default=512, help="Tile size (default: 512)")
    parser.add_argument(
        "--physical-size",
        type=float,
        nargs=2,
        metavar=("X", "Y"),
        help="Pixel size in µm (overrides file metadata)",
    )
    args = parser.parse_args()

    try:
        wsi_to_ometiff(
            args.input,
            output_path=args.output,
            tile_size=args.tile_size,
            physical_size_xy=args.physical_size,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
