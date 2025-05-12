import requests
from datetime import datetime, timedelta
from io import BytesIO
from dagster import asset, AssetMaterialization, Output, AssetExecutionContext

# Assuming 'storage' is in PYTHONPATH and contains wrm_data/config.py
# Adjust this import based on your project structure if necessary.
# If these configs change often or per environment, consider Dagster's config system.
try:
    from storage.wrm_data.config import (
        WRM_STATIONS_DATA_URL,
        WRM_STATIONS_S3_PREFIX,
        WRM_STATION_BASE_FILENAME,
        BUCKET_NAME
    )
except ImportError:
    # Provide default values or raise a more specific error if config is critical
    print("Warning: Could not import configuration from storage.wrm_data.config. Using placeholders.")
    WRM_STATIONS_DATA_URL = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"
    WRM_STATIONS_S3_PREFIX = "bike-data/gen_info/"
    WRM_STATION_BASE_FILENAME = "wrm_stations"
    BUCKET_NAME = "disband-yodel-botanical"

# If you created the MinIOResource in resources.py:
# from wrm_pipeline.resources import MinIOResource # Replaced with S3Resource
from dagster_aws.s3.resources import S3Resource

@asset(
    name="wrm_stations_raw_data",
    description="Downloads station data from WRM and uploads it to S3.",
    group_name="wrm_ingestion",
    compute_kind="python",
)
def wrm_stations_raw_data_asset(context: AssetExecutionContext, s3_resource: S3Resource) -> Output[str]:
    """
    Downloads WRM station data and stores it in S3.
    Returns the S3 object name.
    """
    logger = context.log

    # Get current time and adjust for GMT+1
    current_time = datetime.utcnow() + timedelta(hours=1)
    
    # Create filename with GMT+1 time
    filename = f"{WRM_STATION_BASE_FILENAME}_{current_time.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    
    # URL to download
    url = WRM_STATIONS_DATA_URL
    
    # Download the content
    logger.info(f"Attempting to download data from: {url}")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        data_to_upload = BytesIO(response.content)
        data_length = len(response.content)
        logger.info(f"Successfully downloaded {filename} ({data_length} bytes).")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading file from {url}: {e}")
        raise

    s3_client = s3_resource.get_client()
    bucket_name = BUCKET_NAME 
    if not bucket_name:
        logger.error("Error: BUCKET_NAME is not configured (e.g. via storage.wrm_data.config).")
        raise ValueError("BUCKET_NAME is not configured.")

    # Ensure bucket exists
    # s3_resource.ensure_bucket_exists(bucket_name, logger) # S3Resource does not have this method directly. Use s3_client.head_bucket or similar if needed.

    # Create date path for S3 object name
    date_path = current_time.strftime('%Y/%m/%d')
    s3_object_name = f"{WRM_STATIONS_S3_PREFIX}{date_path}/{filename}"
    
    # Upload to S3
    logger.info(f"Uploading '{filename}' to S3 bucket '{bucket_name}' as '{s3_object_name}'...")
    try:
        data_to_upload.seek(0)  # Reset BytesIO position to the beginning
        s3_client.put_object(
            Bucket=bucket_name, # Changed from bucket_name
            Key=s3_object_name,  # Changed from object_name
            Body=data_to_upload, # Changed from data
            ContentLength=data_length, # Changed from length
            ContentType="text/plain" # Changed from content_type
        )
        logger.info(f"Successfully uploaded '{filename}' to '{bucket_name}/{s3_object_name}'.")

        # Materialize the asset
        metadata = {
            "s3_bucket": bucket_name,
            "s3_key": s3_object_name,
            "source_url": url,
            "download_timestamp_utc": (current_time - timedelta(hours=1)).isoformat(),
            "filename": filename,
            "size_bytes": data_length,
        }
        context.add_output_metadata(metadata=metadata, output_name="result")


        return Output(value=s3_object_name, metadata=metadata)

    except Exception as e: 
        logger.error(f"Error uploading file '{filename}' to S3: {e}")
        raise

