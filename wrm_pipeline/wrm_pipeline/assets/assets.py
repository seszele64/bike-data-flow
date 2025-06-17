"""
Asset definitions aggregator module.
Imports all assets from submodules and re-exports them for convenience.
"""

# Import assets from different modules
from .stations import (
    wrm_stations_raw_data_asset,
    wrm_stations_processed_data_all_asset,
    wrm_stations_data_asset,
    wrm_bikes_data_asset,
    wrm_stations_enhanced_data_all_asset,
)

# DuckDB assets
from .duckdb import (
    create_duckdb_enhanced_views,
    query_station_summary,
    bike_density_spatial_analysis,
    bike_density_map
)

__all__ = [
    # Station assets
    "wrm_stations_raw_data_asset",
    "wrm_stations_processed_data_all_asset",
    "wrm_stations_data_asset",
    "wrm_bikes_data_asset",
    "wrm_stations_enhanced_data_all_asset",
    # DuckDB assets
    "create_duckdb_enhanced_views",
    "query_station_summary",
    "bike_density_spatial_analysis",
    "bike_density_map"
]