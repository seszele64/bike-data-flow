from dagster import asset, AssetExecutionContext, MaterializeResult, MetadataValue
import pandas as pd
from io import BytesIO
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List
import pandera as pa
from pandera import Column, DataFrameSchema, Check

from ...config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from ...models.stations import enhanced_daily_schema
from .commons import daily_partitions
from .processed_all import wrm_stations_processed_data_all_asset

# S3 key patterns
WRM_STATIONS_RAW_S3_KEY_PATTERN = f"{WRM_STATIONS_S3_PREFIX}raw/dt={{partition_date}}/wrm_stations_{{timestamp}}.txt"
WRM_STATIONS_PROCESSED_S3_KEY_PATTERN = f"{WRM_STATIONS_S3_PREFIX}processed/all/dt={{partition_date}}/all_processed_{{timestamp}}.parquet"
WRM_STATIONS_ENHANCED_S3_KEY_PATTERN = f"{WRM_STATIONS_S3_PREFIX}enhanced/all/dt={{partition_date}}/all_enhanced_{{timestamp}}.parquet"

@asset(
    name="wrm_stations_enhanced_data_all",
    partitions_def=daily_partitions,
    compute_kind="pandas",
    group_name="enhanced_data",
    required_resource_keys={"s3_resource"},
    deps=[wrm_stations_processed_data_all_asset],
)
def wrm_stations_enhanced_data_all_asset(
    context: AssetExecutionContext, 
    wrm_stations_processed_data_all: pd.DataFrame
) -> MaterializeResult:
    """Add record type classification, validate with schema, and save enhanced data to S3"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # Get partition key (date) from context
    partition_date = context.partition_key
    
    # Print columns at the beginning for debugging
    context.log.info(f"Input DataFrame columns: {list(wrm_stations_processed_data_all.columns)}")
    context.log.info(f"Input DataFrame shape: {wrm_stations_processed_data_all.shape}")
    context.log.info(f"Input DataFrame dtypes:\n{wrm_stations_processed_data_all.dtypes}")
    
    # Print full head without truncation
    with pd.option_context('display.max_columns', None, 'display.width', None, 'display.max_colwidth', None):
        context.log.info(f"Input DataFrame head (full):\n{wrm_stations_processed_data_all.head()}")
    
    try:
        df = wrm_stations_processed_data_all.copy()
        
        # Add record type classification
        df['record_type'] = 'unknown'
        
        # Station records: ID is integer and name doesn't begin with 'BIKE'
        station_mask = (
            df['station_id'].str.isdigit() & 
            ~df['name'].str.startswith('BIKE', na=False)
        )
        df.loc[station_mask, 'record_type'] = 'station'
        
        # Bike records: ID begins with 'fb' and name begins with 'BIKE'
        bike_mask = (
            df['station_id'].str.startswith('fb', na=False) & 
            df['name'].str.startswith('BIKE', na=False)
        )
        df.loc[bike_mask, 'record_type'] = 'bike'
        
        # Validate with Pandera schema
        try:
            # Add all required columns before validation
            df['date'] = pd.to_datetime(partition_date)
            df['processed_at'] = datetime.now()
            
            # Reorder columns to match the enhanced_daily_schema order
            expected_columns = [
                'station_id', 'name', 'timestamp', 'gmt_local_diff_sec', 
                'gmt_servertime_diff_sec', 'lat', 'lon', 'bikes', 'spaces', 
                'installed', 'locked', 'temporary', 'total_docks', 
                'givesbonus_acceptspedelecs_fbbattlevel', 'pedelecs', 'record_type',
                's3_source_key', 'file_timestamp', 'date', 'processed_at'
            ]
            df = df[expected_columns]
            
            # Now validate the complete dataframe with enhanced_daily_schema
            validated_df = enhanced_daily_schema.validate(df, lazy=True)
            context.log.info(f"Pandera validation passed for {len(validated_df)} records")
            
        except pa.errors.SchemaErrors as e:
            context.log.error(f"Pandera validation failed: {e}")
            
            # Log detailed validation errors
            for error in e.failure_cases.itertuples():
                context.log.error(f"Validation error - Column: {error.column}, Check: {error.check}, Value: {error.failure_case}")
            
            # Log column info for debugging
            context.log.error(f"DataFrame columns: {list(df.columns)}")
            context.log.error(f"DataFrame dtypes: {df.dtypes}")
            
            raise ValueError("Schema validation failed")
        
        # Use the timestamp from the most recent file for the processed data filename
        latest_file_timestamp = validated_df['file_timestamp'].max()
        
        # Log the classification results
        station_count = len(validated_df[validated_df['record_type'] == 'station'])
        bike_count = len(validated_df[validated_df['record_type'] == 'bike'])
        unknown_count = len(validated_df[validated_df['record_type'] == 'unknown'])
        
        context.log.info(f"Classified data for partition {partition_date}: {station_count} stations, {bike_count} bikes, {unknown_count} unknown")
        
        # Generate S3 key and upload
        timestamp = latest_file_timestamp.strftime("%Y%m%d_%H%M%S")
        s3_key = WRM_STATIONS_ENHANCED_S3_KEY_PATTERN.format(partition_date=partition_date, timestamp=timestamp)
        
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
        
        context.log.info(f"Enhanced data saved to S3: {s3_key}")
        
        # Get unique processed files information
        processed_files_info = validated_df.groupby(['s3_source_key', 'file_timestamp']).size().reset_index(name='records_count')
        
        # Add metadata
        return MaterializeResult(
            asset_key=context.asset_key,
            metadata={
                "columns": list(validated_df.columns),
                "column_types": dict(validated_df.dtypes.astype(str)),
                "total_records": len(validated_df),
                "station_records": station_count,
                "bike_records": bike_count,
                "unknown_records": unknown_count,
                "partition_date": partition_date,
                "file_timestamp": latest_file_timestamp.isoformat(),
                "processing_timestamp": datetime.now().isoformat(),
                "s3_key": s3_key,
                "processed_files_count": len(processed_files_info),
                "processed_files": processed_files_info['s3_source_key'].tolist(),
                "files_record_counts": dict(zip(processed_files_info['s3_source_key'], processed_files_info['records_count'])),
                "data_preview": MetadataValue.md(validated_df.head().to_markdown())
            }
        )
        
    except Exception as e:
        context.log.error(f"Failed to enhance and save station data for partition {partition_date}: {e}")
        raise