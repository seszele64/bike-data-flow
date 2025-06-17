from dagster import asset, AssetExecutionContext, DailyPartitionsDefinition
import requests
import pandas as pd
import ftfy
from io import StringIO, BytesIO
from datetime import datetime
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List
import pandera as pa
from pandera import Column, DataFrameSchema, Check
import hashlib

from ...config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from ...models.stations import processed_data_schema
from .commons import daily_partitions
from .enhanced_all import wrm_stations_enhanced_data_all_asset


@asset(
    name="daily_station_data",
    deps=[wrm_stations_enhanced_data_all_asset],
    partitions_def=daily_partitions,
    compute_kind="pandas",
    group_name="enhanced_data",
    required_resource_keys={"s3_resource"},
    io_manager_key="iceberg_io_manager",  # Add this to use Iceberg I/O manager
    metadata={
        "partition_expr": "date",
        "partition_spec_update_mode": "update",
        "schema_update_mode": "update"
    }
)
def wrm_stations_data_asset(context: AssetExecutionContext) -> pd.DataFrame:
    """Extract and store station-only data from processed data"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # Get partition key (date) from context
    partition_date = context.partition_key
    
    try:
        # Look for all processed data files for this partition date
        processed_s3_prefix = f"{WRM_STATIONS_S3_PREFIX}processed/all/dt={partition_date}/"
        
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=processed_s3_prefix
        )
        
        if 'Contents' not in response or len(response['Contents']) == 0:
            context.log.info(f"No processed data found in S3 for partition {partition_date}")
            
            # Add metadata to indicate no data was processed
            context.add_output_metadata({
                "partition_date": partition_date,
                "status": "no_upstream_data",
                "station_records": 0
            })
            
            return pd.DataFrame()
        
        # Get all files for this partition date
        all_files = response['Contents']
        context.log.info(f"Found {len(all_files)} processed files for partition {partition_date}")
        
        # Combine data from all files for this partition
        all_dataframes = []
        for file_info in all_files:
            file_key = file_info['Key']
            context.log.info(f"Loading processed file: {file_key}")
            
            try:
                # Download and read each file
                file_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=file_key)
                file_df = pd.read_parquet(BytesIO(file_response['Body'].read()))
                all_dataframes.append(file_df)
                
            except Exception as e:
                context.log.warning(f"Failed to load file {file_key}: {e}")
                continue
        
        if not all_dataframes:
            context.log.warning(f"No valid processed data files found for partition {partition_date}")
            return pd.DataFrame()
        
        # Combine all dataframes
        df = pd.concat(all_dataframes, ignore_index=True)
        context.log.info(f"Combined {len(df)} total records from {len(all_dataframes)} files")
        
        # Filter for station records only based on record_type column
        stations_df = df[df['record_type'] == 'station'].copy()
        
        # Drop the record_type column as it's no longer needed
        stations_df = stations_df.drop(columns=['record_type'])
        
        if stations_df.empty:
            context.log.warning(f"No station records found in processed data for partition {partition_date}")
            
            # Add metadata for empty result
            context.add_output_metadata({
                "partition_date": partition_date,
                "status": "no_station_records",
                "station_records": 0
            })
            
            return pd.DataFrame()
        
        context.log.info(f"Filtered to {len(stations_df)} station records")
        
        # Convert timestamp columns to microsecond precision for Iceberg compatibility
        datetime_columns = stations_df.select_dtypes(include=['datetime64[ns]']).columns
        for col in datetime_columns:
            stations_df[col] = stations_df[col].astype('datetime64[us]')
        
        # Add metadata
        context.add_output_metadata({
            "station_records": len(stations_df),
            "partition_date": partition_date,
            "data_date": stations_df['date'].iloc[0].isoformat() if not stations_df.empty else None,
            "processed_files_count": len(all_dataframes)
        })
        
        # The DataFrame will be automatically saved to Iceberg by the I/O manager
        return stations_df
        
    except Exception as e:
        context.log.error(f"Failed to process stations data for partition {partition_date}: {e}")
        raise
