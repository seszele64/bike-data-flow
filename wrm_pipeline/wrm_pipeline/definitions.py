from dagster import Definitions, load_assets_from_modules, ConfigurableResource
from dagster_aws.s3.resources import S3Resource
import os
from dotenv import load_dotenv
import psycopg2
from contextlib import contextmanager

from .assets import assets
from .sensors.stations_sensor import s3_raw_stations_sensor
from .sensors.s3_processed_to_postgres_sensor import s3_processed_stations_sensor
from .config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
    S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION_NAME
)

# Load environment variables from .env file in parent directory
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
load_dotenv(dotenv_path)

class PostgreSQLResource(ConfigurableResource):
    host: str
    port: int
    database: str
    user: str
    password: str
    
    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password
        )
        try:
            yield conn
        finally:
            conn.close()

all_assets = load_assets_from_modules([assets])

# Determine if a region name is configured and should be passed
s3_resource_config = {
    "endpoint_url": S3_ENDPOINT_URL,
    "aws_access_key_id": S3_ACCESS_KEY_ID,
    "aws_secret_access_key": S3_SECRET_ACCESS_KEY,
    "region_name": S3_REGION_NAME
}

defs = Definitions(
    assets=all_assets,
    sensors=[s3_raw_stations_sensor, s3_processed_stations_sensor],
    resources={
        "s3_resource": S3Resource(**s3_resource_config),
        "postgres_resource": PostgreSQLResource(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        ),
    }
)
