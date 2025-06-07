from dagster import Definitions, load_assets_from_modules
from dagster_aws.s3.resources import S3Resource
import os
from dotenv import load_dotenv
from pathlib import Path

# import wrm_pipeline.assets as assets
from .assets import assets
from .sensors.stations_sensor import s3_raw_stations_sensor

# Load environment variables from .env file in parent directory
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
load_dotenv(dotenv_path)

# Get S3/MinIO configuration from environment variables
S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT_URL')
S3_ACCESS_KEY_ID = os.environ.get('S3_ACCESS_KEY_ID')
S3_SECRET_ACCESS_KEY = os.environ.get('S3_SECRET_ACCESS_KEY')
S3_REGION_NAME = os.environ.get('S3_REGION_NAME', None)  # Optional, can be None

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
    sensors=[s3_raw_stations_sensor],  # Add your sensor here
    resources={
        "s3_resource": S3Resource(**s3_resource_config),
    }
)
