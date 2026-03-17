#!/usr/bin/env python3
"""Convert OMERO quantification CSVs to mcmicro format for gater.

OMERO CSVs have a fundamentally different column structure than the mcmicro
format that gater expects:

- Cell ID column is ``object`` instead of ``CellID``
- Coordinates are ``Centroid_x``/``Centroid_y`` instead of
  ``X_centroid``/``Y_centroid``
- Each channel has 8 statistical columns (Mean, Max, Min, P25, Median,
  P75, P95, Std_dev) instead of a single intensity column
- Morphology columns use different names (``Major_axis`` vs
  ``MajorAxisLength``)

This module detects OMERO CSVs and converts them in-place so the rest of
the gater pipeline (config generation, channel matching, normalization)
works without modification.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

_MEAN_SUFFIX = '_Mean_intensity'

# Morphology columns to keep, with optional renaming.
# Keys are OMERO names, values are mcmicro names.
_MORPHOLOGY_RENAME = {
    'Area': 'Area',
    'Major_axis': 'MajorAxisLength',
    'Minor_axis': 'MinorAxisLength',
    'Eccentricity': 'Eccentricity',
    'Solidity': 'Solidity',
    'Extent': 'Extent',
    'Orientation': 'Orientation',
}


def is_omero_csv(header):
    """Return True if *header* looks like an OMERO quantification CSV.

    Detection criteria: first column is ``object`` **and** at least one
    column ends with ``_Mean_intensity``.  This ensures mcmicro CSVs
    (which start with ``CellID``) pass through unchanged.

    Args:
        header: List of column name strings (e.g. from
            ``csv.DictReader.fieldnames``).

    Returns:
        bool
    """
    if not header:
        return False
    if header[0] != 'object':
        return False
    return any(col.endswith(_MEAN_SUFFIX) for col in header)


def discover_channels(header):
    """Extract channel names from OMERO ``*_Mean_intensity`` columns.

    Handles multi-word channel names like ``Pan_Cytokeratin_Mean_intensity``
    by stripping only the ``_Mean_intensity`` suffix.

    Args:
        header: List of column name strings.

    Returns:
        List of channel prefix strings in header order
        (e.g. ``['DAPI', 'HLA', 'Pan_Cytokeratin']``).
    """
    channels = []
    for col in header:
        if col.endswith(_MEAN_SUFFIX):
            prefix = col[: -len(_MEAN_SUFFIX)]
            channels.append(prefix)
    return channels


def omero_csv_to_mcmicro(input_path, output_path=None):
    """Convert an OMERO quantification CSV to mcmicro format.

    Transformations applied:

    1. Rename ``object`` -> ``CellID``, ``Centroid_x`` -> ``X_centroid``,
       ``Centroid_y`` -> ``Y_centroid``
    2. Keep only ``*_Mean_intensity`` per channel, strip suffix
       (e.g. ``R_Mean_intensity`` -> ``R``)
    3. Keep morphology columns (Area, Major_axis -> MajorAxisLength, etc.)
    4. Drop everything else (prob, geometry, Bbox_*, tile_index, ...)
    5. Output column order: ``CellID, X_centroid, Y_centroid,
       [channels...], Area, MajorAxisLength, MinorAxisLength,
       Eccentricity, Solidity, Extent, Orientation``

    Args:
        input_path: Path to the input OMERO CSV file.
        output_path: Optional output path. If None, overwrites *input_path*
            in-place.

    Returns:
        Path to the written CSV file.
    """
    input_path = Path(input_path)
    if output_path is None:
        output_path = input_path
    else:
        output_path = Path(output_path)

    df = pd.read_csv(input_path)

    # Discover channel names from header
    channels = discover_channels(list(df.columns))
    if not channels:
        raise ValueError(
            f"No *_Mean_intensity columns found in {input_path}. "
            "Is this an OMERO quantification CSV?"
        )

    # Build rename mapping
    rename = {
        'object': 'CellID',
        'Centroid_x': 'X_centroid',
        'Centroid_y': 'Y_centroid',
    }
    # Channel mean columns: DAPI_Mean_intensity -> DAPI
    for ch in channels:
        rename[f'{ch}{_MEAN_SUFFIX}'] = ch
    # Morphology columns
    rename.update(_MORPHOLOGY_RENAME)

    df = df.rename(columns=rename)

    # Build output column order
    out_cols = ['CellID', 'X_centroid', 'Y_centroid']
    out_cols.extend(channels)
    morph_cols = list(_MORPHOLOGY_RENAME.values())
    for col in morph_cols:
        if col in df.columns:
            out_cols.append(col)

    # Select only the columns we want (drops all others)
    df = df[out_cols]

    df.to_csv(output_path, index=False)

    num_rows = len(df)
    print(
        f"Converted OMERO CSV -> mcmicro: {len(channels)} channels, "
        f"{num_rows} cells, {len(out_cols)} columns\n"
        f"  {input_path} -> {output_path}",
        file=sys.stderr,
    )
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Convert OMERO quantification CSV to mcmicro format."
    )
    parser.add_argument("input", type=Path, help="Input OMERO CSV file")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output CSV path (default: overwrite input in-place)"
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    # Verify it's actually an OMERO CSV before converting
    probe_df = pd.read_csv(args.input, nrows=0)
    header = list(probe_df.columns)
    if not is_omero_csv(header):
        print(
            f"Not an OMERO CSV (first column: '{header[0]}'). "
            "No conversion needed.",
            file=sys.stderr,
        )
        sys.exit(0)

    try:
        omero_csv_to_mcmicro(args.input, output_path=args.output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
