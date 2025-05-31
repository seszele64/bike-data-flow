import requests
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from dagster import asset, Output, AssetExecutionContext, AssetKey  # Add AssetKey import
from dagster_aws.s3.resources import S3Resource
import pandas as pd
import ftfy  # Add this to your requirements.txt if not already there

try:
    from ..config import (
        WRM_STATIONS_DATA_URL,
        WRM_STATIONS_S3_PREFIX,
        WRM_STATION_BASE_FILENAME,
        BUCKET_NAME,
    )
except ImportError:
    # Provide default values or raise a more specific error if config is critical
    print("Warning: Could not import configuration. Using placeholders.")

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
    date_str = current_time.strftime('%Y-%m-%d')
    s3_object_name = f"{WRM_STATIONS_S3_PREFIX}dt={date_str}/{filename}"
    
    # Upload to S3
    logger.info(f"Uploading '{filename}' to S3 bucket '{bucket_name}' as '{s3_object_name}'...")
    try:
        data_to_upload.seek(0)  # Reset BytesIO position to the beginning
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_object_name,
            Body=data_to_upload,
            ContentLength=data_length,
            ContentType="text/plain"
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

@asset(
    name="wrm_stations_processed",
    description="Processes the raw station data to clean encoding and format as structured data.",
    group_name="wrm_processing",
    compute_kind="python",
    deps=["wrm_stations_raw_data"],  # Depend on the raw data asset
)
def wrm_stations_processed_asset(context: AssetExecutionContext, s3_resource: S3Resource) -> Output[str]:
    """
    Processes the raw WRM station data by:
    - Fixing text encoding issues
    - Parsing into a structured DataFrame
    - Cleaning and formatting columns
    
    Returns the S3 object name of the processed parquet file.
    """
    logger = context.log
    
    # Get the latest raw data file information from upstream asset
    upstream_output = context.instance.get_latest_materialization_event(
        asset_key=AssetKey("wrm_stations_raw_data")  # Use AssetKey object instead of string
    )
    
    if not upstream_output:
        logger.error("No upstream raw data found")
        raise ValueError("No upstream raw data found")
    
    # Extract S3 location from upstream asset metadata
    metadata = upstream_output.asset_materialization.metadata
    s3_bucket = metadata["s3_bucket"].value
    s3_key = metadata["s3_key"].value
    
    logger.info(f"Processing data from {s3_bucket}/{s3_key}")
    
    # Download the raw data from S3
    s3_client = s3_resource.get_client()
    try:
        response = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
        raw_data = response['Body'].read().decode('utf-8')
        logger.info(f"Successfully downloaded raw data ({len(raw_data)} bytes)")
    except Exception as e:
        logger.error(f"Error downloading raw data from S3: {e}")
        raise
    
    # Process the data using the provided method chaining
    try:
        result_df = (pd.read_csv(
                        StringIO(ftfy.fix_text(raw_data, normalization='NFKC')), 
                        quotechar='"', 
                        encoding='utf-8', 
                        delimiter=',', 
                        skipinitialspace=True
                    )
                    # Rename '#id' column to 'id'
                    .rename(columns={'#id': 'id'})
                    # Extract timestamp components directly
                    .assign(
                        unix_timestamp=lambda df: df['timestamp|gmt_local_diff_sec|gmt_servertime_diff_sec'].str.split('|').str[0].astype(float),
                        gmt_local_diff_sec=lambda df: df['timestamp|gmt_local_diff_sec|gmt_servertime_diff_sec'].str.split('|').str[1],
                        gmt_servertime_diff_sec=lambda df: df['timestamp|gmt_local_diff_sec|gmt_servertime_diff_sec'].str.split('|').str[2]
                    )
                    # Convert timestamp and add it as a new column
                    .assign(
                        timestamp=lambda df: pd.to_datetime(df['unix_timestamp'], unit='s')
                    )
                    # Drop unnecessary columns
                    .drop(columns=['timestamp|gmt_local_diff_sec|gmt_servertime_diff_sec', 'unix_timestamp'])
                    # Reorder columns
                    .pipe(lambda df: df[['id', 'timestamp', 'gmt_local_diff_sec', 'gmt_servertime_diff_sec'] + 
                                      [col for col in df.columns if col not in ['id', 'timestamp', 'gmt_local_diff_sec', 'gmt_servertime_diff_sec']]])
                   )
        
        logger.info(f"Successfully processed data: {len(result_df)} rows, {result_df.shape[1]} columns")
    except Exception as e:
        logger.error(f"Error processing data: {e}")
        raise
    
    # Create processed data filename and S3 key
    current_time = datetime.utcnow() + timedelta(hours=1)
    base_filename = s3_key.split('/')[-1].replace('.txt', '')
    processed_filename = f"{base_filename}_processed.parquet"
    date_path = current_time.strftime('%Y/%m/%d')
    processed_s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/{date_path}/{processed_filename}"
    
    # Save to S3
    try:
        # Convert DataFrame to parquet and upload
        parquet_buffer = BytesIO()
        result_df.to_parquet(parquet_buffer, index=False)
        parquet_buffer.seek(0)
        
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=processed_s3_key,
            Body=parquet_buffer,
            ContentType="application/parquet"
        )
        
        logger.info(f"Successfully uploaded processed data to {s3_bucket}/{processed_s3_key}")
        
        # Materialize the asset
        metadata = {
            "s3_bucket": s3_bucket,
            "s3_key": processed_s3_key,
            "source_s3_key": s3_key,
            "rows": len(result_df),
            "columns": result_df.shape[1],
            "processing_timestamp_utc": datetime.utcnow().isoformat(),
            "filename": processed_filename,
        }
        context.add_output_metadata(metadata=metadata, output_name="result")
        
        return Output(value=processed_s3_key, metadata=metadata)
    
    except Exception as e:
        logger.error(f"Error uploading processed data to S3: {e}")
        raise