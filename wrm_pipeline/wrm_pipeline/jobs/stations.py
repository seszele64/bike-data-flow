from dagster import define_asset_job
from ..assets.stations.processed_all import wrm_stations_processed_data_all_asset
from ..assets.stations.enhanced_all import wrm_stations_enhanced_data_all_asset

# Define a job that materializes both processed and enhanced data assets
wrm_stations_processing_job = define_asset_job(
    name="wrm_stations_processing_job",
    selection=[
        wrm_stations_processed_data_all_asset,
        wrm_stations_enhanced_data_all_asset
    ],
    description="Process raw WRM station data and create enhanced dataset"
)