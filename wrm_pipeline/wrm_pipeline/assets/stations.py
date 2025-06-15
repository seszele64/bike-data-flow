from dagster import asset, AssetExecutionContext, DailyPartitionsDefinition
from dagster_aws.s3.resources import S3Resource
import requests
import pandas as pd
import ftfy
from io import StringIO, BytesIO
from datetime import datetime
import os
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List
import pandera as pa
from pandera import Column, DataFrameSchema, Check
import pytz

from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

# Define daily partitions with your local timezone
daily_partitions = DailyPartitionsDefinition(
    start_date="2025-06-01",
    timezone="Europe/Warsaw"  # or your local timezone
)

class WRMStationRecord(BaseModel):
    """Pydantic model for individual WRM station record"""
    station_id: str = Field(alias="#id")
    timestamp_tz: str
    name: str
    bikes: int
    spaces: int
    installed: bool
    locked: bool
    temporary: bool
    total_docks: int
    
    class Config:
        allow_population_by_field_name = True

class WRMRawSchema(BaseModel):
    """Pydantic model for the complete WRM API response"""
    stations: List[WRMStationRecord]
    fetch_timestamp: datetime
    total_records: int
    
    @classmethod
    @field_validator('stations')
    def validate_stations_not_empty(cls, v):
        if not v:
            raise ValueError("Stations list cannot be empty")
        return v

# Define Pandera schema for WRM stations data validation
wrm_stations_schema = DataFrameSchema({
    "station_id": Column(str, nullable=False),
    "timestamp": Column("datetime64[ns]", nullable=False),
    "name": Column(str, nullable=False),
    "lat": Column(float, nullable=True),  # May not be present in all data
    "lon": Column(float, nullable=True),  # May not be present in all data
    "bikes": Column(int, Check.greater_than_or_equal_to(0), nullable=False),
    "spaces": Column(int, Check.greater_than_or_equal_to(0), nullable=False),
    "installed": Column(bool, Check.isin([True, False]), nullable=False),
    "locked": Column(bool, Check.isin([True, False]), nullable=False),
    "temporary": Column(bool, Check.isin([True, False]), nullable=False),
    "total_docks": Column(int, Check.greater_than_or_equal_to(1), nullable=False),
    "givesbonus_acceptspedelecs_fbbattlevel": Column(
        int, 
        Check(lambda x: (x == -1) | (x == 0) | (x >= 0)), 
        nullable=True
    ),
    "pedelecs": Column(int, Check.greater_than_or_equal_to(0), nullable=True),
    "timezone_1": Column(str, nullable=True),
    "timezone_2": Column(str, nullable=True),
    "date": Column("datetime64[ns]", nullable=False),  # Changed from str to datetime64[ns]
    "processed_at": Column("datetime64[ns]", nullable=False),
    "s3_source_key": Column(str, nullable=False)
}, strict=False)  # Allow additional columns

@asset(
    name="wrm_stations_raw_data",
    # No partitions_def here - it only fetches current data
    compute_kind="requests",
    group_name="wrm_data_acquisition",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_raw_data_asset(context: AssetExecutionContext) -> str:
    """Download raw station data from WRM API and store in S3 without validation"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # API endpoint for WRM bike stations
    api_url = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"
    
    try:
        # Download data from API
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        
        # Fix encoding issues
        fixed_data = ftfy.fix_text(response.text)
        
        # Capture current time for file naming
        current_time = datetime.now()
        timestamp = current_time.strftime("%Y-%m-%d_%H-%M-%S")
        date_partition = current_time.strftime("%Y-%m-%d")
        
        # Define S3 key for raw data using current date
        s3_key = f"{WRM_STATIONS_S3_PREFIX}raw/dt={date_partition}/wrm_stations_{timestamp}.txt"
        
        # Upload raw data to S3
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=fixed_data.encode('utf-8'),
            ContentType='text/plain'
        )
        
        context.log.info(f"Raw station data uploaded to S3: {s3_key}")
        
        # Add output metadata
        context.add_output_metadata({
            "fetch_timestamp": current_time.isoformat(),
            "data_date": date_partition,  # Track what date this data represents
            "s3_key": s3_key,
            "data_size_bytes": len(fixed_data.encode('utf-8'))
        })
        
        return s3_key
        
    except Exception as e:
        context.log.error(f"Failed to download or upload raw station data: {e}")
        raise

@asset(
    name="wrm_stations_validated_data",
    # Remove the direct dependency on raw data asset
    partitions_def=daily_partitions,
    compute_kind="validation",
    group_name="wrm_data_processing",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_validated_data_asset(context: AssetExecutionContext) -> str:
    """Validate raw station data with Pydantic and store validated data partitioned by date"""
    
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
        
        # Get the most recent raw data file for this date
        latest_file = max(response['Contents'], key=lambda x: x['LastModified'])
        raw_data_key = latest_file['Key']
        
        context.log.info(f"Using raw data file: {raw_data_key}")
        
        # Download raw data from S3
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=raw_data_key)
        raw_data = response['Body'].read().decode('utf-8')
        
        # Parse the raw data as CSV
        df = pd.read_csv(StringIO(raw_data))
        
        # Log the actual column names for debugging
        context.log.info(f"CSV columns found: {list(df.columns)}")
        context.log.info(f"First few rows shape: {df.head().shape}")
        
        # Prepare data for Pydantic validation
        stations_list = []
        current_time = datetime.now()
        
        # Create individual station records
        for idx, row in df.iterrows():
            try:
                # Handle different possible column names
                station_data = {}
                
                # Map station ID - check multiple possible column names
                if '#id' in row and pd.notna(row['#id']):
                    station_data["#id"] = str(row['#id'])
                elif 'id' in row and pd.notna(row['id']):
                    station_data["#id"] = str(row['id'])
                elif 'station_id' in row and pd.notna(row['station_id']):
                    station_data["#id"] = str(row['station_id'])
                else:
                    context.log.warning(f"No valid station ID found in row {idx}, skipping")
                    continue
                
                # Map timestamp - check multiple possible column names
                if 'timestamp_tz' in row and pd.notna(row['timestamp_tz']):
                    station_data["timestamp_tz"] = str(row['timestamp_tz'])
                elif 'timestamp' in row and pd.notna(row['timestamp']):
                    station_data["timestamp_tz"] = str(row['timestamp'])
                elif 'time' in row and pd.notna(row['time']):
                    station_data["timestamp_tz"] = str(row['time'])
                else:
                    # Use current timestamp if none found
                    station_data["timestamp_tz"] = str(int(current_time.timestamp()))
                
                # Map other required fields with fallbacks
                station_data["name"] = str(row.get('name', row.get('station_name', f'Station_{station_data["#id"]}')))
                station_data["bikes"] = int(row.get('bikes', row.get('available_bikes', 0)))
                station_data["spaces"] = int(row.get('spaces', row.get('free_docks', row.get('available_docks', 0))))
                
                # Handle boolean fields
                station_data["installed"] = _parse_boolean(row.get('installed', row.get('is_installed', True)))
                station_data["locked"] = _parse_boolean(row.get('locked', row.get('is_locked', False)))
                station_data["temporary"] = _parse_boolean(row.get('temporary', row.get('is_temporary', False)))
                
                # Handle total docks
                station_data["total_docks"] = int(row.get('total_docks', 
                                                        row.get('capacity', 
                                                               station_data["bikes"] + station_data["spaces"])))
                
                # Ensure total_docks is at least 1
                if station_data["total_docks"] <= 0:
                    station_data["total_docks"] = max(1, station_data["bikes"] + station_data["spaces"])
                
                # Create and validate the station record
                station_record = WRMStationRecord(**station_data)
                stations_list.append(station_record)
                
            except (ValidationError, ValueError, KeyError) as e:
                context.log.warning(f"Skipping invalid record at row {idx}: {e}")
                context.log.debug(f"Row data: {dict(row)}")
                continue
        
        if not stations_list:
            context.log.error(f"No valid station records found after validation. Total rows processed: {len(df)}")
            context.log.error(f"Available columns: {list(df.columns)}")
            context.log.error(f"Sample row: {dict(df.iloc[0]) if len(df) > 0 else 'No data'}")
            raise ValueError("No valid station records found after validation")
        
        # Validate the complete structure with WRMRawSchema
        try:
            validated_data = WRMRawSchema(
                stations=stations_list,
                fetch_timestamp=current_time,
                total_records=len(stations_list)
            )
            context.log.info(f"Pydantic validation passed for {len(stations_list)} records")
        except ValidationError as e:
            context.log.error(f"Pydantic validation failed: {e}")
            raise
        
        # Generate S3 key for validated data
        timestamp = current_time.strftime("%Y%m%d_%H%M%S")
        validated_s3_key = f"{WRM_STATIONS_S3_PREFIX}validated/dt={partition_date}/validated_{timestamp}.json"
        
        # Upload validated data as JSON
        import json
        validated_json = json.dumps(validated_data.dict(), indent=2, default=str)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=validated_s3_key,
            Body=validated_json.encode('utf-8'),
            ContentType='application/json'
        )
        
        context.log.info(f"Validated data saved to S3: {validated_s3_key}")
        
        # Add metadata
        context.add_output_metadata({
            "total_records": len(stations_list),
            "partition_date": partition_date,
            "validation_timestamp": current_time.isoformat(),
            "source_raw_key": raw_data_key,
            "input_columns": list(df.columns),
            "input_rows": len(df)
        })
        
        return validated_s3_key
        
    except Exception as e:
        context.log.error(f"Failed to validate station data for partition {partition_date}: {e}")
        raise

def _parse_boolean(value):
    """Helper method to parse boolean values from various formats"""
    if isinstance(value, bool):
        return value
    elif isinstance(value, str):
        return value.lower() in ('true', '1', 'yes', 'on')
    elif isinstance(value, (int, float)):
        return bool(value)
    else:
        return False

@asset(
    name="wrm_stations_all_processed",
    deps=[wrm_stations_validated_data_asset],
    partitions_def=daily_partitions,
    compute_kind="pandas",
    group_name="wrm_data_processing",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_all_processed_asset(context: AssetExecutionContext, wrm_stations_validated_data: str) -> str:
    """Process validated station data and store combined data in S3 as parquet"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # Get partition key (date) from context
    partition_date = context.partition_key
    
    try:
        # Download validated data from S3
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_stations_validated_data)
        validated_json = response['Body'].read().decode('utf-8')
        
        # Parse the validated JSON data
        import json
        validated_data = json.loads(validated_json)
        
        # Convert stations data to DataFrame
        stations_list = validated_data['stations']
        df = pd.DataFrame(stations_list)
        
        # Process the data
        df = df.rename(columns={'station_id': 'station_id'})  # Keep original name from Pydantic
        
        # Convert timestamp_tz to proper datetime
        df['timestamp'] = pd.to_datetime(df['timestamp_tz'], unit='s')
        
        # Convert partition date to datetime format for proper validation
        df['date'] = pd.to_datetime(partition_date)
        df['processed_at'] = datetime.now()
        
        # Add missing columns with default values
        if 'lat' not in df.columns:
            df['lat'] = None
        if 'lon' not in df.columns:
            df['lon'] = None
        if 'givesbonus_acceptspedelecs_fbbattlevel' not in df.columns:
            df['givesbonus_acceptspedelecs_fbbattlevel'] = -1
        if 'pedelecs' not in df.columns:
            df['pedelecs'] = 0
        if 'timezone_1' not in df.columns:
            df['timezone_1'] = None
        if 'timezone_2' not in df.columns:
            df['timezone_2'] = None
        
        # Add S3 source key for traceability
        df['s3_source_key'] = wrm_stations_validated_data
        
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
        
        # Log the classification results
        station_count = len(df[df['record_type'] == 'station'])
        bike_count = len(df[df['record_type'] == 'bike'])
        unknown_count = len(df[df['record_type'] == 'unknown'])
        
        context.log.info(f"Classified data for partition {partition_date}: {station_count} stations, {bike_count} bikes, {unknown_count} unknown")
        
        # Validate DataFrame with Pandera schema
        try:
            validated_df = wrm_stations_schema.validate(df, lazy=True)
            context.log.info(f"Pandera validation passed for {len(validated_df)} records")
            
            # Add validation metadata
            context.add_output_metadata({
                "pandera_validation_passed": True,
                "validation_errors": None
            })
            
        except pa.errors.SchemaErrors as e:
            context.log.error(f"Pandera validation failed: {e}")
            
            # Log detailed validation errors
            for error in e.failure_cases.itertuples():
                context.log.error(f"Validation error - Column: {error.column}, Check: {error.check}, Value: {error.failure_case}")
            
            # Add validation metadata
            context.add_output_metadata({
                "pandera_validation_passed": False,
                "validation_errors": str(e),
                "failed_rows": len(e.failure_cases)
            })
            
            # Continue processing with original dataframe but log the issues
            validated_df = df
            context.log.warning("Continuing with unvalidated data due to schema errors")
        
        # Generate S3 key for all processed data using partition
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        all_s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/all/dt={partition_date}/all_processed_{timestamp}.parquet"
        
        # Upload all processed data
        all_buffer = BytesIO()
        validated_df.to_parquet(all_buffer, index=False)
        all_buffer.seek(0)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=all_s3_key,
            Body=all_buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        context.log.info(f"All processed data saved to S3: {all_s3_key}")
        
        # Add metadata
        context.add_output_metadata({
            "total_records": len(validated_df),
            "station_records": station_count,
            "bike_records": bike_count,
            "unknown_records": unknown_count,
            "partition_date": partition_date
        })
        
        return all_s3_key
        
    except Exception as e:
        context.log.error(f"Failed to process all station data for partition {partition_date}: {e}")
        raise

@asset(
    name="wrm_stations_data",
    deps=[wrm_stations_all_processed_asset],
    partitions_def=daily_partitions,  # Add this line
    compute_kind="pandas",
    group_name="wrm_data_processing",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_data_asset(context: AssetExecutionContext, wrm_stations_all_processed: str) -> str:
    """Extract and store station-only data from processed data"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # Get partition key (date) from context
    partition_date = context.partition_key
    
    try:
        # Download processed data from S3
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_stations_all_processed)
        df = pd.read_parquet(BytesIO(response['Body'].read()))
        
        # Filter for station records only
        stations_df = df[df['record_type'] == 'station'].copy()
        
        # Drop the record_type column as it's no longer needed
        stations_df = stations_df.drop(columns=['record_type'])
        
        if stations_df.empty:
            context.log.warning(f"No station records found in processed data for partition {partition_date}")
            return ""
        
        # Generate S3 key for stations data using partition date
        # Use partition_date instead of datetime.now() for consistency
        timestamp = partition_date.replace('-', '') + "_000000"  # Use partition date for timestamp
        stations_s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/stations/dt={partition_date}/stations_{timestamp}.parquet"
        
        # Upload stations data
        stations_buffer = BytesIO()
        stations_df.to_parquet(stations_buffer, index=False)
        stations_buffer.seek(0)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=stations_s3_key,
            Body=stations_buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        context.log.info(f"Stations data saved to S3: {stations_s3_key}")
        
        # Add metadata
        context.add_output_metadata({
            "station_records": len(stations_df),
            "partition_date": partition_date,
            "data_date": stations_df['date'].iloc[0].isoformat() if not stations_df.empty else None
        })
        
        return stations_s3_key
        
    except Exception as e:
        context.log.error(f"Failed to process stations data for partition {partition_date}: {e}")
        raise

@asset(
    name="wrm_bikes_data",
    deps=[wrm_stations_all_processed_asset],
    partitions_def=daily_partitions,  # Add this line
    compute_kind="pandas",
    group_name="wrm_data_processing",
    required_resource_keys={"s3_resource"}
)
def wrm_bikes_data_asset(context: AssetExecutionContext, wrm_stations_all_processed: str) -> str:
    """Extract and store bike-only data from processed data"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # Get partition key (date) from context
    partition_date = context.partition_key
    
    try:
        # Download processed data from S3
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_stations_all_processed)
        df = pd.read_parquet(BytesIO(response['Body'].read()))
        
        # Filter for bike records only
        bikes_df = df[df['record_type'] == 'bike'].copy()
        
        # Drop the record_type column as it's no longer needed
        bikes_df = bikes_df.drop(columns=['record_type'])
        
        if bikes_df.empty:
            context.log.warning(f"No bike records found in processed data for partition {partition_date}")
            return ""
        
        # Generate S3 key for bikes data using partition date
        # Use partition_date instead of datetime.now() for consistency
        timestamp = partition_date.replace('-', '') + "_000000"  # Use partition date for timestamp
        bikes_s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/bikes/dt={partition_date}/bikes_{timestamp}.parquet"
        
        # Upload bikes data
        bikes_buffer = BytesIO()
        bikes_df.to_parquet(bikes_buffer, index=False)
        bikes_buffer.seek(0)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=bikes_s3_key,
            Body=bikes_buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        context.log.info(f"Bikes data saved to S3: {bikes_s3_key}")
        
        # Add metadata
        context.add_output_metadata({
            "bike_records": len(bikes_df),
            "partition_date": partition_date,
            "data_date": bikes_df['date'].iloc[0].isoformat() if not bikes_df.empty else None
        })
        
        return bikes_s3_key
        
    except Exception as e:
        context.log.error(f"Failed to process bikes data for partition {partition_date}: {e}")
        raise