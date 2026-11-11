from dagster import define_asset_job
from ..assets.stations.processed_all import wrm_stations_processed_data_all_asset
from ..assets.stations.enhanced_all import wrm_stations_enhanced_data_all_asset
from ..assets.stations.raw_all import wrm_stations_raw_data_asset
from ..assets.stations.latest_all import (
    wrm_stations_processed_data_latest_asset,
    wrm_stations_enhanced_data_latest_asset,
)
from ..assets.duckdb.create_enhanced_views import create_duckdb_enhanced_views
from ..assets.dashboard import evidence_data_snapshot

# Transform job (T1-A): materializes the processed and enhanced datasets from
# previously ingested raw data. The raw asset is deliberately NOT selected:
# it is unpartitioned, while this job runs on daily partitions, so a raw
# re-fetch here would hit the API on every scheduled daily tick.
wrm_stations_processing_job = define_asset_job(
    name="wrm_stations_processing_job",
    selection=[
        wrm_stations_processed_data_all_asset,
        wrm_stations_enhanced_data_all_asset
    ],
    description="Process raw WRM station data and create enhanced dataset"
)

# Ingest job (T1-A): raw-only and unpartitioned, mirroring the raw asset
# (assets/stations/raw_all.py), which fetches the latest snapshot from the
# WRM API and stores it in S3 without any daily partition semantics.
wrm_stations_ingest_job = define_asset_job(
    name="wrm_stations_ingest_job",
    selection=[wrm_stations_raw_data_asset],
    description="Ingest raw WRM station data from the API into S3"
)

# Latest-data refresh job: the full 15-minute pipeline. Every asset here is
# unpartitioned, so the job has no partitions_def and its schedule can emit a
# bare RunRequest (no partition_key). Selection order mirrors the dependency
# chain: raw fetch → latest processed → latest enhanced → DuckDB views →
# dashboard snapshot. The daily archive job (wrm_stations_processing_job) and
# its partitioned assets are deliberately NOT part of this loop, and neither
# are evidence_build/evidence_deploy (GitHub Pages polls the dataset manifest
# and rebuilds only when the data hash changes).
wrm_stations_latest_refresh_job = define_asset_job(
    name="wrm_stations_latest_refresh_job",
    selection=[
        wrm_stations_raw_data_asset,
        wrm_stations_processed_data_latest_asset,
        wrm_stations_enhanced_data_latest_asset,
        create_duckdb_enhanced_views,
        evidence_data_snapshot,
    ],
    description=(
        "Fetch raw WRM station data and refresh the latest processed/enhanced "
        "datasets, DuckDB views and dashboard snapshot"
    ),
)
