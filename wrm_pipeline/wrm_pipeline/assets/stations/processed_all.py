from dagster import asset, AssetExecutionContext, MaterializeResult, MetadataValue
import requests
import pandas as pd
import ftfy
from io import BytesIO
from datetime import datetime
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List
import pandera as pa
from pandera import Column, DataFrameSchema, Check
import hashlib

from ...config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from ...models.stations import enhanced_daily_schema, processed_data_schema
from .commons import daily_partitions
from .transforms import parse_raw_stations_csv

# S3 key patterns
WRM_STATIONS_RAW_S3_KEY_PATTERN = f"{WRM_STATIONS_S3_PREFIX}raw/dt={{partition_date}}/wrm_stations_{{timestamp}}.txt"
WRM_STATIONS_PROCESSED_S3_KEY_PATTERN = f"{WRM_STATIONS_S3_PREFIX}processed/all/dt={{partition_date}}/all_processed_{{timestamp}}.parquet"
WRM_STATIONS_ENHANCED_S3_KEY_PATTERN = f"{WRM_STATIONS_S3_PREFIX}enhanced/all/dt={{partition_date}}/all_enhanced_{{timestamp}}.parquet"

# =============================================================================
# Vault Integration Notes for Station Assets
# =============================================================================
# 
# The station assets (raw_all, processed_all, enhanced_all) are designed to work
# with or without Vault integration. Sensitive configuration is retrieved via:
# 
# 1. Direct config.py values (for non-sensitive config like BUCKET_NAME)
# 2. Config.py helper functions (get_database_config, get_storage_config) which
#    automatically fall back to environment variables if Vault is not enabled
# 3. WRMAPIConfig class in raw_all.py for API URL (supports Vault if enabled)
# 
# To enable Vault for these assets:
# 1. Set VAULT_ENABLED=true in .env
# 2. Configure VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID
# 3. Store secrets in Vault at:
#    - bike-data-flow/production/database (for DB config)
#    - bike-data-flow/production/storage (for S3 config)
#    - bike-data-flow/production/api (for API config)
# 
# Example Vault secret structure:
# {
#   "host": "localhost",
#   "port": 5432,
#   "username": "bike_data_flow",
#   "password": "your-password",
#   "database": "bike_data"
# }

@asset(
    name="wrm_stations_processed_data_all",
    partitions_def=daily_partitions,
    compute_kind="pandas",
    group_name="processed_data",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_processed_data_all_asset(context: AssetExecutionContext) -> pd.DataFrame:
    """Process and validate raw station data into a combined DataFrame persisted to S3 as Parquet"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # Get partition key (date) from context
    partition_date = context.partition_key
    
    try:
        # Look for raw data files for this partition date
        raw_s3_prefix = f"{WRM_STATIONS_S3_PREFIX}raw/dt={partition_date}/"
        
        # List objects in the raw data partition
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=raw_s3_prefix
        )
        
        if 'Contents' not in response or not response['Contents']:
            raise FileNotFoundError(f"No raw data found for partition date {partition_date}")
        
        # Get ALL raw data files for this date
        raw_files = response['Contents']
        context.log.info(f"Found {len(raw_files)} raw data files for partition date {partition_date}")
        
        # Sort files by LastModified to process them in chronological order
        raw_files = sorted(raw_files, key=lambda x: x['LastModified'])
        
        all_dataframes = []
        processed_files = []
        
        # Process each raw file
        for file_info in raw_files:
            raw_data_key = file_info['Key']
            context.log.info(f"Processing raw data file: {raw_data_key}")
            
            try:
                # Extract timestamp from raw file name
                import re
                timestamp_match = re.search(r'wrm_stations_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.txt$', raw_data_key)
                if timestamp_match:
                    timestamp_str = timestamp_match.group(1)
                    file_timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                    context.log.info(f"Extracted timestamp from filename: {file_timestamp}")
                else:
                    file_timestamp = file_info['LastModified'].replace(tzinfo=None)
                    context.log.warning(f"Could not extract timestamp from filename, using file modification time: {file_timestamp}")
                
                # Download raw data from S3
                response = s3_client.get_object(Bucket=BUCKET_NAME, Key=raw_data_key)
                data_text = response['Body'].read().decode('utf-8')
                
                # Parse and transform the data using the shared helper
                df = parse_raw_stations_csv(
                    data_text,
                    log=context.log,
                    source_label=raw_data_key,
                )

                if df is None:
                    continue

                # Add file source information before appending
                df['s3_source_key'] = raw_data_key
                df['file_timestamp'] = file_timestamp
                
                all_dataframes.append(df)
                processed_files.append({
                    "file_key": raw_data_key,
                    "file_timestamp": file_timestamp,
                    "records_count": len(df)
                })
                    
            except Exception as e:
                context.log.error(f"Failed to process file {raw_data_key}: {e}")
                continue
        
        if not all_dataframes:
            context.log.error(f"No valid data found after processing all files for partition {partition_date}")
            raise ValueError("No valid data found after processing")
        
        # Combine all dataframes
        df = pd.concat(all_dataframes, ignore_index=True)
        
        # Convert timestamp from float to datetime for schema validation
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
        
        context.log.info(f"Combined {len(df)} total records for partition {partition_date}")
        
        # Validate DataFrame against schema before returning
        try:
            context.log.info("Validating DataFrame against processed_data_schema...")
            validated_df = processed_data_schema.validate(df)
            context.log.info("Schema validation successful!")
        except pa.errors.SchemaError as e:
            context.log.error(f"Schema validation failed: {e}")
            # Log additional details about the validation error
            context.log.error(f"Schema failures: {e.failure_cases}")
            raise ValueError(f"DataFrame does not match processed_data_schema: {e}")
        
        # Use the timestamp from the most recent file for the processed data filename
        # (mirrors enhanced_all.py: max over per-file timestamps equals
        # validated_df['file_timestamp'].max() since schema validation either
        # returns all rows or raises)
        latest_file_timestamp = max(f['file_timestamp'] for f in processed_files)
        
        # Generate S3 key and upload
        timestamp = latest_file_timestamp.strftime("%Y%m%d_%H%M%S")
        s3_key = WRM_STATIONS_PROCESSED_S3_KEY_PATTERN.format(partition_date=partition_date, timestamp=timestamp)
        
        # Upload to S3
        buffer = BytesIO()
        validated_df.to_parquet(buffer, index=False)
        buffer.seek(0)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        context.log.info(f"Processed data saved to S3: {s3_key}")
        
        # Add basic metadata
        context.add_output_metadata({
            "columns": list(validated_df.columns),
            "column_types": dict(validated_df.dtypes.astype(str)),
            "total_records": len(validated_df),
            "partition_date": partition_date,
            "processed_files_count": len(processed_files),
            "processed_files": [f['file_key'] for f in processed_files],
            "s3_key": s3_key,
            "data_preview": MetadataValue.md(validated_df.head().to_markdown()),
            "schema_validation": "PASSED"
        })
        
        return validated_df
        
    except Exception as e:
        context.log.error(f"Failed to process station data for partition {partition_date}: {e}")
        raise

