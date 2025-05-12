from dagster import Definitions, load_assets_from_modules
from dagster_aws.s3.resources import S3Resource
import os
from dotenv import load_dotenv
from pathlib import Path

# import wrm_pipeline.assets as assets
from .assets import assets

# Attempt to import S3/MinIO configuration from your storage config
# Ensure these variables are defined in storage/wrm_data/config.py
# (which likely loads them from your .env file)
# Load environment variables from .env file in parent directory
dotenv_path = Path(__file__).parents[2] / '.env'
load_dotenv(dotenv_path)

# Get S3/MinIO configuration from environment variables
HETZNER_ENDPOINT_URL = os.environ.get('HETZNER_ENDPOINT_URL')
HETZNER_ACCESS_KEY_ID = os.environ.get('HETZNER_ACCESS_KEY_ID')
HETZNER_SECRET_ACCESS_KEY = os.environ.get('HETZNER_SECRET_ACCESS_KEY')

# add https:// to the endpoint URL if not present
if HETZNER_ENDPOINT_URL and not HETZNER_ENDPOINT_URL.startswith("https://"):
    HETZNER_ENDPOINT_URL = "https://" + HETZNER_ENDPOINT_URL


all_assets = load_assets_from_modules([assets])

# Determine if a region name is configured and should be passed
s3_resource_config = {
    "endpoint_url": HETZNER_ENDPOINT_URL,
    "aws_access_key_id": HETZNER_ACCESS_KEY_ID,
    "aws_secret_access_key": HETZNER_SECRET_ACCESS_KEY
}
# if S3_REGION_NAME: # Uncomment if you add S3_REGION_NAME to your config
# s3_resource_config["region_name"] = S3_REGION_NAME

defs = Definitions(
    assets=all_assets,
    resources={
        "s3_resource": S3Resource(**s3_resource_config),
    }
)
