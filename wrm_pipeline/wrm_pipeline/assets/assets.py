"""
Asset definitions aggregator module.
Imports all assets from submodules and re-exports them for convenience.
"""

# Import assets from different modules
from .stations import wrm_stations_raw_data_asset, wrm_stations_processed_asset

# Re-export all assets
__all__ = [
    "wrm_stations_raw_data_asset",
    "wrm_stations_processed_asset"
]