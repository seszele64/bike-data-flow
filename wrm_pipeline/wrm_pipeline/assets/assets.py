"""
Asset definitions aggregator module.
Imports all assets from submodules and re-exports them for convenience.
"""

# Import assets from different modules
from .stations import (
    wrm_stations_raw_data_asset,
    wrm_stations_validated_data_asset,
    wrm_stations_all_processed_asset,
    wrm_stations_data_asset,
    wrm_bikes_data_asset
)
from .stations_deduplicated import s3_processed_stations_list, wrm_stations_daily_deduplicated
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
    "wrm_stations_validated_data_asset",
    "wrm_stations_all_processed_asset",
    "wrm_stations_data_asset",
    "wrm_bikes_data_asset",
    "s3_processed_stations_list",
    "wrm_stations_daily_deduplicated",
    "postgres_connection",
    "bike_stations_table",
    "bike_failures_table",
    "load_stations_to_postgres",
    "stations_data_summary"
]