from dagster import Definitions, EnvVar, load_assets_from_modules
import os
from dotenv import load_dotenv

from .assets import assets
from .resources import (
    s3_resource,
    postgres_resource,
    duckdb_io_manager,
    duckdb_s3_io_manager,
    duckdb_hybrid_io_manager,
    s3_io_manager,
    hive_partitioned_s3_io_manager
)
from .sensors.stations import wrm_stations_raw_data_sensor
from .jobs.stations import wrm_stations_processing_job
from .vault import vault_secrets_resource

# Load environment variables from .env file in parent directory
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
load_dotenv(dotenv_path)

all_assets = load_assets_from_modules([assets])

defs = Definitions(
    assets=all_assets,
    jobs=[
        wrm_stations_processing_job,
    ],
    sensors=[
        wrm_stations_raw_data_sensor,
    ],
    resources={
        key: resource
        for key, resource in {
            "s3_resource": s3_resource,
            "s3": s3_resource,  # Add this - s3_io_manager expects key "s3"
            "postgres_resource": postgres_resource,
            "duckdb_io_manager": duckdb_io_manager,
            "duckdb_s3_io_manager": duckdb_s3_io_manager,
            "duckdb_hybrid_io_manager": duckdb_hybrid_io_manager,
            "s3_io_manager": s3_io_manager,
            "hive_partitioned_s3_io_manager": hive_partitioned_s3_io_manager,
            "vault": vault_secrets_resource().configured(
                {
                    "vault_addr": EnvVar("VAULT_ADDR"),
                    "auth_method": EnvVar("VAULT_AUTH_METHOD"),
                    "role_id": EnvVar("VAULT_ROLE_ID"),
                    "secret_id": EnvVar("VAULT_SECRET_ID"),
                    "timeout": 30,
                    "retries": 3,
                    "cache_ttl": 300,
                    "verify": True,
                }
            ),
        }.items()
        # Skip resources backed by integrations that are not installed
        if resource is not None
    },
    schedules=[]
)
