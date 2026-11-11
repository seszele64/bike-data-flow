"""Unpartitioned "latest data" assets for WRM station snapshots.

The daily ``processed_all`` / ``enhanced_all`` assets re-list and reprocess an
entire ``dt=YYYY-MM-DD`` S3 partition. These latest assets are unpartitioned:
they consume the single S3 key returned by ``wrm_stations_raw_data`` (no S3
listing), reuse the shared parser and schema contracts, and overwrite stable
``processed/latest/`` and ``enhanced/latest/`` parquet keys. The daily archive
assets and their keys are deliberately left untouched.
"""

import re
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd
import pandera as pa
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from ...config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from ...models.stations import enhanced_daily_schema, processed_data_schema
from .transforms import parse_raw_stations_csv

# Stable, unpartitioned output keys (overwritten on every materialization).
WRM_STATIONS_PROCESSED_LATEST_S3_KEY = (
    f"{WRM_STATIONS_S3_PREFIX}processed/latest/all_processed_latest.parquet"
)
WRM_STATIONS_ENHANCED_LATEST_S3_KEY = (
    f"{WRM_STATIONS_S3_PREFIX}enhanced/latest/all_enhanced_latest.parquet"
)

_RAW_KEY_TIMESTAMP_RE = re.compile(
    r"wrm_stations_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.txt$"
)
_RAW_KEY_DATE_RE = re.compile(r"/dt=(\d{4}-\d{2}-\d{2})/")

# Column order required by ``enhanced_daily_schema`` (ordered=True).
ENHANCED_COLUMNS = [
    "station_id",
    "name",
    "timestamp",
    "gmt_local_diff_sec",
    "gmt_servertime_diff_sec",
    "lat",
    "lon",
    "bikes",
    "spaces",
    "installed",
    "locked",
    "temporary",
    "total_docks",
    "givesbonus_acceptspedelecs_fbbattlevel",
    "pedelecs",
    "record_type",
    "s3_source_key",
    "file_timestamp",
    "date",
    "processed_at",
]


def _raw_file_timestamp(raw_key: str, log) -> datetime:
    """Derive a naive UTC timestamp from the raw key, falling back to ``now``."""
    match = _RAW_KEY_TIMESTAMP_RE.search(raw_key)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
    log.warning(
        f"Could not extract timestamp from raw key {raw_key}; using current UTC time"
    )
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _data_date(processed_df: pd.DataFrame) -> pd.Timestamp:
    """Data date for the enhanced row set: the raw ``dt=`` date, else file date."""
    source_key = str(processed_df["s3_source_key"].iloc[0])
    match = _RAW_KEY_DATE_RE.search(source_key)
    if match:
        return pd.Timestamp(datetime.strptime(match.group(1), "%Y-%m-%d").date())
    return pd.Timestamp(processed_df["file_timestamp"].max()).normalize()


@asset(
    name="wrm_stations_processed_data_latest",
    compute_kind="pandas",
    group_name="processed_data",
    required_resource_keys={"s3_resource"},
)
def wrm_stations_processed_data_latest_asset(
    context: AssetExecutionContext, wrm_stations_raw_data: str
) -> pd.DataFrame:
    """Parse the current raw S3 object into validated, unpartitioned parquet."""
    s3_client = context.resources.s3_resource
    raw_key = wrm_stations_raw_data

    if not raw_key or not isinstance(raw_key, str):
        raise ValueError(
            "wrm_stations_raw_data did not provide an S3 key for the latest snapshot"
        )

    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=raw_key)
        data_text = response["Body"].read().decode("utf-8")
    except Exception as e:
        context.log.error(f"Failed to download raw station data object: {raw_key}")
        raise RuntimeError(
            f"Failed to read raw station data object {raw_key}: {e}"
        ) from e

    try:
        df = parse_raw_stations_csv(data_text, log=context.log, source_label=raw_key)
    except Exception as e:
        raise ValueError(f"Failed to parse raw station data from {raw_key}: {e}") from e

    if df is None:
        raise ValueError(
            f"Raw station data object {raw_key} contained no parseable rows"
        )

    file_timestamp = _raw_file_timestamp(raw_key, context.log)
    df = df.copy()
    df["s3_source_key"] = raw_key
    df["file_timestamp"] = file_timestamp
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

    try:
        validated_df = processed_data_schema.validate(df)
    except pa.errors.SchemaError as e:
        context.log.error(f"Schema validation failed for {raw_key}: {e}")
        raise ValueError(
            f"Latest data from {raw_key} does not match processed_data_schema: {e}"
        ) from e

    buffer = BytesIO()
    validated_df.to_parquet(buffer, index=False)

    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
        Body=buffer.getvalue(),
        ContentType="application/octet-stream",
    )

    context.log.info(
        f"Latest processed data saved to S3: {WRM_STATIONS_PROCESSED_LATEST_S3_KEY}"
    )
    context.add_output_metadata(
        {
            "columns": list(validated_df.columns),
            "column_types": dict(validated_df.dtypes.astype(str)),
            "total_records": len(validated_df),
            "raw_s3_key": raw_key,
            "file_timestamp": file_timestamp.isoformat(),
            "s3_key": WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
            "schema_validation": "PASSED",
            "data_preview": MetadataValue.md(validated_df.head().to_markdown()),
        }
    )

    return validated_df


@asset(
    name="wrm_stations_enhanced_data_latest",
    compute_kind="pandas",
    group_name="enhanced_data",
    required_resource_keys={"s3_resource"},
)
def wrm_stations_enhanced_data_latest_asset(
    context: AssetExecutionContext, wrm_stations_processed_data_latest: pd.DataFrame
) -> MaterializeResult:
    """Classify, validate and overwrite the stable latest enhanced parquet."""
    s3_client = context.resources.s3_resource

    if (
        wrm_stations_processed_data_latest is None
        or wrm_stations_processed_data_latest.empty
    ):
        raise ValueError("Processed latest data contains no rows to enhance")

    df = wrm_stations_processed_data_latest.copy()

    df["record_type"] = "unknown"
    station_mask = df["station_id"].str.isdigit() & ~df["name"].str.startswith(
        "BIKE", na=False
    )
    df.loc[station_mask, "record_type"] = "station"
    bike_mask = df["station_id"].str.startswith("fb", na=False) & df[
        "name"
    ].str.startswith("BIKE", na=False)
    df.loc[bike_mask, "record_type"] = "bike"

    data_date = _data_date(df)
    df["date"] = data_date
    df["processed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    df = df[ENHANCED_COLUMNS]

    try:
        validated_df = enhanced_daily_schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        context.log.error(f"Pandera validation failed: {e}")
        for error in e.failure_cases.itertuples():
            context.log.error(
                f"Validation error - Column: {error.column}, "
                f"Check: {error.check}, Value: {error.failure_case}"
            )
        raise ValueError(
            "Latest enhanced data does not match enhanced_daily_schema"
        ) from e

    buffer = BytesIO()
    validated_df.to_parquet(buffer, index=False)

    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
        Body=buffer.getvalue(),
        ContentType="application/octet-stream",
    )

    station_count = len(validated_df[validated_df["record_type"] == "station"])
    bike_count = len(validated_df[validated_df["record_type"] == "bike"])
    unknown_count = len(validated_df[validated_df["record_type"] == "unknown"])

    context.log.info(
        f"Latest enhanced data saved to S3: {WRM_STATIONS_ENHANCED_LATEST_S3_KEY} "
        f"({station_count} stations, {bike_count} bikes, {unknown_count} unknown)"
    )

    return MaterializeResult(
        metadata={
            "columns": list(validated_df.columns),
            "column_types": dict(validated_df.dtypes.astype(str)),
            "total_records": len(validated_df),
            "station_records": station_count,
            "bike_records": bike_count,
            "unknown_records": unknown_count,
            "date": data_date.isoformat(),
            "processed_at": validated_df["processed_at"].max().isoformat(),
            "row_s3_source_key": str(validated_df["s3_source_key"].iloc[0]),
            "s3_key": WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
            "schema_validation": "PASSED",
            "data_preview": MetadataValue.md(validated_df.head().to_markdown()),
        }
    )
