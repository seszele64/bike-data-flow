from dagster import Definitions, load_assets_from_modules
import os
from dotenv import load_dotenv

from .assets import assets
from .sensors.stations_sensor import s3_raw_stations_sensor
from .sensors.s3_processed_to_postgres_sensor import s3_processed_stations_sensor
from .resources import (
    s3_resource,
    postgres_resource,
    iceberg_io_manager,
    duckdb_io_manager,
    duckdb_s3_io_manager,
    duckdb_hybrid_io_manager,
    s3_io_manager,
    hive_partitioned_s3_io_manager  # Add this import
)
    
# Load environment variables from .env file in parent directory
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
load_dotenv(dotenv_path)

all_assets = load_assets_from_modules([assets])

defs = Definitions(
    assets=all_assets,
    sensors=[s3_raw_stations_sensor, s3_processed_stations_sensor],
    resources={
        "s3_resource": s3_resource,
        "s3": s3_resource,  # Add this - s3_io_manager expects key "s3"
        "postgres_resource": postgres_resource,
        "iceberg_io_manager": iceberg_io_manager,
        "duckdb_io_manager": duckdb_io_manager,
        "duckdb_s3_io_manager": duckdb_s3_io_manager,
        "duckdb_hybrid_io_manager": duckdb_hybrid_io_manager,
        "s3_io_manager": s3_io_manager,
        "hive_partitioned_s3_io_manager": hive_partitioned_s3_io_manager,  # Add this
    }
)
