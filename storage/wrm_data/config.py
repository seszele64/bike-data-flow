import os
from dotenv import load_dotenv
import os.path

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(dotenv_path=dotenv_path, override=True)  # .env is in root directory

# s3
HETZNER_ENDPOINT = os.environ.get('HETZNER_ENDPOINT')
HETZNER_ENDPOINT_URL = os.environ.get('HETZNER_ENDPOINT_URL')
HETZNER_ACCESS_KEY_ID = os.environ.get('HETZNER_ACCESS_KEY_ID')
HETZNER_SECRET_ACCESS_KEY = os.environ.get('HETZNER_SECRET_ACCESS_KEY')

# --- URLs for data sources ---
WRM_FAILURES_DATA_URL = "https://www.wroclaw.pl/open-data/39605eb0-8055-4733-bb02-04b96791d36a/WRM_usterki.csv"
WRM_STATIONS_DATA_URL = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"

# --- File naming ---
WRM_FAILURES_BASE_FILENAME = "wrm_failures"
WRM_STATION_BASE_FILENAME = "station_data"
CSV_EXTENSION = "csv"

# --- S3/MinIO Configuration ---
BUCKET_NAME = os.environ.get('BUCKET_NAME')

# Storage paths
WRM_FAILURES_TARGET_FOLDER = 'bike-data/failures/'
if not WRM_FAILURES_TARGET_FOLDER.endswith('/'):
    WRM_FAILURES_TARGET_FOLDER += '/'

# S3 storage paths
WRM_STATIONS_S3_PREFIX = "bike-data/gen_info/"

# Path to .env file for configuration
ENV_PATH = dotenv_path