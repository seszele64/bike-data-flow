"""
Asset definitions aggregator module.
Imports all assets from submodules and re-exports them for convenience.
"""

# Import assets from different modules
from .stations import wrm_stations_raw_data_asset, wrm_stations_processed_asset
from .processed_stations import s3_processed_stations_list, wrm_stations_data, wrm_stations_postgres

# Re-export all assets
__all__ = [
    "wrm_stations_raw_data_asset",
    "wrm_stations_processed_asset",
    "s3_processed_stations_list",
    "wrm_stations_data",
    "wrm_stations_postgres"
]