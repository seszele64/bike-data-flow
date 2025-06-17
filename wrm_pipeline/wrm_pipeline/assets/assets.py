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
from .stations_deduplicated import (
    wrm_stations_deduplicated_asset,
    deduplication_quality_report_asset
)
from .postgres_assets import (
    postgres_connection,
    bike_stations_table,
    bike_failures_table,
    load_stations_to_postgres,
    stations_data_summary
)

# Re-export all assets
# from .sample_iceberg_asset import (
#     sample_bike_stations_iceberg,
#     processed_bike_stations_iceberg,
#     my_table,
#     my_analysis
# )

# Import Iceberg assets
# from .iceberg_assets import (
#     stations_iceberg_table, 
#     bikes_iceberg_table, 
#     all_processed_iceberg_table,
#     daily_station_summary,
#     daily_bike_summary
# )

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
    # Deduplicated station assets
    "wrm_stations_deduplicated_asset",
    "deduplication_quality_report_asset",
    # PostgreSQL assets
    "postgres_connection",
    "bike_stations_table",
    "bike_failures_table",
    "load_stations_to_postgres",
    "stations_data_summary",
    # Sample Iceberg assets
    # "sample_bike_stations_iceberg",
    # "processed_bike_stations_iceberg",
    # "my_table",
    # "my_analysis",
    # Iceberg assets
    # "stations_iceberg_table",
    # "bikes_iceberg_table",
    # "all_processed_iceberg_table",
    # "daily_station_summary",
    # "daily_bike_summary",
    # DuckDB assets
    "create_duckdb_enhanced_views",
    "query_station_summary",
    "bike_density_spatial_analysis",
    "bike_density_map"
]