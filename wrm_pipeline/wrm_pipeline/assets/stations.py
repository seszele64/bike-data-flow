from dagster import asset, AssetExecutionContext
from dagster_aws.s3.resources import S3Resource
import requests
import pandas as pd
import ftfy
from io import StringIO, BytesIO
from datetime import datetime
import os

from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

@asset(
    name="wrm_stations_raw_data",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_raw_data_asset(context: AssetExecutionContext) -> str:
    """Download raw station data from WRM API and store in S3"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # API endpoint for WRM bike stations
    api_url = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"
    
    try:
        # Download data from API
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        
        # Capture current time for consistency
        current_time = datetime.now()
        timestamp = current_time.strftime("%Y%m%d_%H%M%S")
        date_partition = current_time.strftime("%Y-%m-%d")
        
        # Define S3 key for raw data
        s3_key = f"{WRM_STATIONS_S3_PREFIX}raw/dt={date_partition}/station_data_{timestamp}.txt"
        
        # Upload raw data to S3
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=response.text.encode('utf-8'),
            ContentType='text/plain'
        )
        
        context.log.info(f"Raw station data uploaded to S3: {s3_key}")
        return s3_key
        
    except Exception as e:
        context.log.error(f"Failed to download or upload raw station data: {e}")
        raise

@asset(
    name="wrm_stations_processed",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_processed_asset(context: AssetExecutionContext, wrm_stations_raw_data: str) -> str:
    """Process raw station data and store in S3 as parquet"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    try:
        # Download raw data from S3
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_stations_raw_data)
        raw_data = response['Body'].read().decode('utf-8')
        
        # Fix encoding issues
        fixed_data = ftfy.fix_text(raw_data)
        
        # Parse CSV data
        df = pd.read_csv(StringIO(fixed_data))
        
        # Extract date from the S3 key (from the partition path)
        # Format: gen_info/raw/dt=2025-06-07/station_data_20250607_123456.txt
        import re
        date_match = re.search(r'dt=(\d{4}-\d{2}-\d{2})', wrm_stations_raw_data)
        if date_match:
            data_date = date_match.group(1)
        else:
            # Fallback to current date if pattern not found
            data_date = datetime.now().strftime("%Y-%m-%d")
        
        # Add date and processing timestamp
        df['date'] = data_date
        df['processed_at'] = datetime.now()
        
        # Generate S3 key for processed data
        date_partition = datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        processed_s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/dt={date_partition}/stations_{timestamp}.parquet"
        
        # Convert to parquet and upload
        parquet_buffer = BytesIO()
        df.to_parquet(parquet_buffer, index=False)
        parquet_buffer.seek(0)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=processed_s3_key,
            Body=parquet_buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        context.log.info(f"Processed {len(df)} station records and saved to S3: {processed_s3_key}")
        return processed_s3_key
        
    except Exception as e:
        context.log.error(f"Failed to process station data: {e}")
        raise