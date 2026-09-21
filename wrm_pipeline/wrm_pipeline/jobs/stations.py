from dagster import define_asset_job
from ..assets.stations.processed_all import wrm_stations_processed_data_all_asset
from ..assets.stations.enhanced_all import wrm_stations_enhanced_data_all_asset
from ..assets.stations.raw_all import wrm_stations_raw_data_asset

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
