"""Reusable transformations for WRM station data.

These helpers intentionally contain no Dagster or S3 dependencies so they can be
shared by partitioned assets (processed_all) and unpartitioned/latest-data
assets alike.
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any

import pandas as pd


def _log(log: Any, level: str, message: str) -> None:
    """Best-effort logging that works with or without a Dagster context logger."""
    if log is not None:
        getattr(log, level)(message)


def _convert_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the raw-file column normalization and dtype conversions.

    ``timestamp`` is deliberately left as float epoch seconds. Callers convert it
    to datetime after concatenating (and deduplicating) files, mirroring the
    historical behavior of ``processed_all``.
    """
    # Rename #id to station_id
    df.rename(columns={'#id': 'station_id'}, inplace=True)

    df['timestamp'] = df['timestamp'].astype(float)
    df['gmt_local_diff_sec'] = df['gmt_local_diff_sec'].astype(int)
    df['gmt_servertime_diff_sec'] = df['gmt_servertime_diff_sec'].astype(int)
    df['lat'] = df['lat'].astype(float)
    df['lon'] = df['lon'].astype(float)
    df['bikes'] = df['bikes'].astype(int)
    df['spaces'] = df['spaces'].astype(int)
    df['installed'] = df['installed'].map({'true': True, 'false': False})
    df['locked'] = df['locked'].map({'true': True, 'false': False})
    df['temporary'] = df['temporary'].map({'true': True, 'false': False})
    df['total_docks'] = df['total_docks'].astype(int)

    # Handle NaN/null and alternate-case values in the combined boolean field
    df['givesbonus_acceptspedelecs_fbbattlevel'] = (
        df['givesbonus_acceptspedelecs_fbbattlevel'].fillna('false')
    )
    df['givesbonus_acceptspedelecs_fbbattlevel'] = df[
        'givesbonus_acceptspedelecs_fbbattlevel'
    ].map({
        'true': True,
        'false': False,
        '': False,  # Handle empty strings
        'True': True,
        'False': False,
    })
    # Fill any remaining unmapped values with False
    df['givesbonus_acceptspedelecs_fbbattlevel'] = df[
        'givesbonus_acceptspedelecs_fbbattlevel'
    ].fillna(False)

    df['pedelecs'] = df['pedelecs'].astype(int)

    # Move name as 2nd column
    return df[
        ['station_id', 'name']
        + [col for col in df.columns if col not in ['station_id', 'name']]
    ]


def parse_raw_stations_csv(
    data_text: str,
    *,
    log: Any = None,
    source_label: str | None = None,
) -> pd.DataFrame | None:
    """Parse one raw WRM stations CSV payload into a typed DataFrame.

    The raw format concatenates the timestamp and GMT offsets in a single
    pipe-delimited field, so that field is split into ``timestamp``,
    ``gmt_local_diff_sec`` and ``gmt_servertime_diff_sec``.

    Returns ``None`` when the payload yields no usable rows, when the header is
    unusable, or when dtype conversion fails. This matches the historical
    per-file "skip and continue" behavior of the processed asset; callers that
    accumulate files should skip ``None`` results and decide whether an overall
    empty result is an error.

    ``source_label`` is used only for log messages (e.g. the S3 key).
    ``timestamp`` remains float epoch seconds; callers convert it to datetime
    after combining files.
    """
    label = source_label or "raw data"

    rows = list(csv.reader(StringIO(data_text)))

    if not rows:
        _log(log, "warning", f"Empty data from {label}")
        return None

    _log(log, "info", f"Rows in {label}: {len(rows)}")

    # Extract header and adjust for split fields
    header = rows[0]
    _log(log, "info", f"Original header: {header}")

    if len(header) < 2:
        _log(log, "warning", f"Unusable header from {label}: {header}")
        return None

    # Replace the second header field with three new headers
    new_header = (
        [header[0], "timestamp", "gmt_local_diff_sec", "gmt_servertime_diff_sec"]
        + header[2:]
    )

    data_rows = []
    for row in rows[1:]:
        # A blank line parses to [], and a truncated row may not even contain the
        # packed-timestamp field, so guard before indexing row[1].
        if len(row) < 2:
            _log(log, "warning", f"Skipping malformed row {row}: too few fields")
            continue

        try:
            # Split the second field by '|'
            timestamp, gmt_local_diff_sec, gmt_servertime_diff_sec = row[1].split('|')
            # Construct the new row
            new_row = (
                [row[0], timestamp, gmt_local_diff_sec, gmt_servertime_diff_sec]
                + row[2:]
            )
            data_rows.append(new_row)
        except ValueError as e:
            _log(log, "warning", f"Could not split row {row}: {e}")
            continue

    if not data_rows:
        _log(log, "warning", f"No valid data rows from {label}")
        return None

    try:
        df = pd.DataFrame(data_rows, columns=new_header)
        df = _convert_columns(df)
    except Exception as e:
        _log(log, "error", f"Failed to convert data types for {label}: {e}")
        return None

    _log(log, "info", f"Successfully processed {len(df)} records from {label}")
    _log(log, "info", f"Final columns: {list(df.columns)}")
    _log(
        log,
        "info",
        "givesbonus_acceptspedelecs_fbbattlevel dtype: "
        f"{df['givesbonus_acceptspedelecs_fbbattlevel'].dtype}",
    )
    _log(
        log,
        "info",
        "givesbonus_acceptspedelecs_fbbattlevel unique values: "
        f"{df['givesbonus_acceptspedelecs_fbbattlevel'].unique()}",
    )

    return df
