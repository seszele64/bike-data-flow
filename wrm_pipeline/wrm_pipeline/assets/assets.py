"""
Asset definitions aggregator module.
Imports all assets from submodules and re-exports them for convenience.
"""

# Import assets from different modules
from .stations import wrm_stations_raw_data_asset, wrm_stations_processed_asset
from .stations_deduplicated import s3_processed_stations_list, wrm_stations_data
from .raw_stations import s3_raw_stations_list, wrm_raw_stations_data, wrm_stations_batch_processor, wrm_stations_processing_summary
from .postgres_assets import (
    postgres_connection,
    bike_stations_table,
    bike_failures_table,
    load_stations_to_postgres,
    stations_data_summary
)

# Re-export all assets
__all__ = [
    "wrm_stations_raw_data_asset",
    "wrm_stations_processed_asset",
    "s3_processed_stations_list",
    "wrm_stations_data",
    "s3_raw_stations_list",
    "wrm_raw_stations_data",
    "wrm_stations_batch_processor",
    "wrm_stations_processing_summary",
    "postgres_connection",
    "bike_stations_table",
    "bike_failures_table",
    "load_stations_to_postgres",
    "stations_data_summary"
]