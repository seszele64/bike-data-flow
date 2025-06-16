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

from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from ..models.stations import wrm_stations_schema

# Define daily partitions with your local timezone
daily_partitions = DailyPartitionsDefinition(
    start_date="2025-05-01"
)

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
        
        # Calculate hash of the new data
        new_data_hash = hashlib.sha256(fixed_data.encode('utf-8')).hexdigest()
        context.log.info(f"New data hash: {new_data_hash}")
        
        # Capture current time for file naming
        current_time = datetime.now()
        timestamp = current_time.strftime("%Y-%m-%d_%H-%M-%S")
        date_partition = current_time.strftime("%Y-%m-%d")
        
        # Check for duplicate data by comparing with the most recent file
        raw_s3_prefix = f"{WRM_STATIONS_S3_PREFIX}raw/dt={date_partition}/"
        
        try:
            # List existing files for today
            response_list = s3_client.list_objects_v2(
                Bucket=BUCKET_NAME,
                Prefix=raw_s3_prefix
            )
            
            if 'Contents' in response_list and response_list['Contents']:
                # Get the most recent file
                most_recent_file = max(response_list['Contents'], key=lambda x: x['LastModified'])
                most_recent_key = most_recent_file['Key']
                
                context.log.info(f"Found most recent file: {most_recent_key}")
                
                # Download and hash the most recent file
                try:
                    recent_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=most_recent_key)
                    recent_data = recent_response['Body'].read().decode('utf-8')
                    recent_data_hash = hashlib.sha256(recent_data.encode('utf-8')).hexdigest()
                    
                    context.log.info(f"Most recent file hash: {recent_data_hash}")
                    
                    # Compare hashes
                    if new_data_hash == recent_data_hash:
                        context.log.info("Data is identical to the most recent file. Skipping upload to avoid duplication.")
                        
                        # Add output metadata for duplicate detection
                        context.add_output_metadata({
                            "fetch_timestamp": current_time.isoformat(),
                            "data_date": date_partition,
                            "duplicate_detected": True,
                            "existing_file": most_recent_key,
                            "data_hash": new_data_hash,
                            "data_size_bytes": len(fixed_data.encode('utf-8')),
                            "skip_reason": "identical_to_recent_file"
                        })
                        
                        # Return the existing file key instead of uploading a new one
                        return most_recent_key
                    else:
                        context.log.info("Data differs from the most recent file. Proceeding with upload.")
                        
                except Exception as e:
                    context.log.warning(f"Could not download or hash recent file {most_recent_key}: {e}")
                    context.log.info("Proceeding with upload due to comparison failure.")
            else:
                context.log.info("No existing files found for today. Proceeding with upload.")
                
        except Exception as e:
            context.log.warning(f"Could not check for existing files: {e}")
            context.log.info("Proceeding with upload due to duplicate check failure.")
        
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
            "data_size_bytes": len(fixed_data.encode('utf-8')),
            "duplicate_detected": False,
            "data_hash": new_data_hash
        })
        
        return s3_key
        
    except Exception as e:
        context.log.error(f"Failed to download or upload raw station data: {e}")
        raise

@asset(
    name="wrm_stations_all_processed",
    partitions_def=daily_partitions,
    compute_kind="pandas",
    group_name="wrm_data_processing",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_all_processed_asset(context: AssetExecutionContext) -> str:
    """Validate raw station data and process into combined data stored in S3 as parquet"""
    
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
        
        # Create Pandera schema for validation after processing
        raw_csv_schema = pa.DataFrameSchema({
            "station_id": pa.Column(str, nullable=False),
            "name": pa.Column(str, nullable=False),
            "timestamp": pa.Column(float, nullable=False),
            "gmt_local_diff_sec": pa.Column(int, nullable=False),
            "gmt_servertime_diff_sec": pa.Column(int, nullable=False),
            "lat": pa.Column(float, nullable=False),
            "lon": pa.Column(float, nullable=False),
            "bikes": pa.Column(int, pa.Check.ge(0), nullable=False),
            "spaces": pa.Column(int, pa.Check.ge(0), nullable=False),
            "installed": pa.Column(bool, nullable=False),
            "locked": pa.Column(bool, nullable=False),
            "temporary": pa.Column(bool, nullable=False),
            "total_docks": pa.Column(int, pa.Check.ge(1), nullable=False),
            "givesbonus_acceptspedelecs_fbbattlevel": pa.Column(bool, nullable=True),
            "pedelecs": pa.Column(int, pa.Check.ge(0), nullable=False),
            "record_type": pa.Column(str, pa.Check.isin(['station', 'bike', 'unknown']), nullable=False),  # Add this line
        }, strict='filter')  # Filter out extra columns
        
        # Process each raw file
        for file_info in raw_files:
            raw_data_key = file_info['Key']
            context.log.info(f"Processing raw data file: {raw_data_key}")
            
            try:
                # Extract timestamp from raw file name
                import re
                import csv
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
                
                # Parse and transform the data using CSV reader
                f = StringIO(data_text)
                reader = csv.reader(f)
                rows = list(reader)
                
                if not rows:
                    context.log.warning(f"Empty data from {raw_data_key}")
                    continue
                
                context.log.info(f"Rows in {raw_data_key}: {len(rows)}")
                
                # Extract header and adjust for split fields
                header = rows[0]
                context.log.info(f"Original header: {header}")
                
                # Replace the second header field with three new headers
                new_header = (
                    [header[0], "timestamp", "gmt_local_diff_sec", "gmt_servertime_diff_sec"]
                    + header[2:]
                )
                
                data_rows = []
                for row in rows[1:]:
                    try:
                        # Split the second field by '|'
                        timestamp, gmt_local_diff_sec, gmt_servertime_diff_sec = row[1].split('|')
                        # Construct the new row
                        new_row = (
                            [row[0], timestamp, gmt_local_diff_sec, gmt_servertime_diff_sec]
                            + row[2:]
                        )
                        data_rows.append(new_row)
                    except ValueError as e:
                        context.log.warning(f"Could not split row {row}: {e}")
                        continue
                
                if not data_rows:
                    context.log.warning(f"No valid data rows from {raw_data_key}")
                    continue
                
                # Create DataFrame
                df = pd.DataFrame(data_rows, columns=new_header)
                
                # Rename #id to station_id
                df.rename(columns={'#id': 'station_id'}, inplace=True)
                
                # Convert data types
                try:
                    df['timestamp'] = df['timestamp'].astype(float)
                    df['gmt_local_diff_sec'] = df['gmt_local_diff_sec'].astype(int)
                    df['gmt_servertime_diff_sec'] = df['gmt_servertime_diff_sec'].astype(int)
                    df['lat'] = df['lat'].astype(float)
                    df['lon'] = df['lon'].astype(float)
                    df['bikes'] = df['bikes'].astype(int)
                    df['spaces'] = df['spaces'].astype(int)
                    df['installed'] = df['installed'].map({'true': True, 'false': False})
                    df['locked'] = df['locked'].map({'true': True, 'false': False})
                    df['temporary'] = df['temporary'].map({'true': True, 'false': False})
                    df['total_docks'] = df['total_docks'].astype(int)
                    
                    # Fix the boolean conversion for givesbonus_acceptspedelecs_fbbattlevel
                    # Handle NaN/null values and map string values to boolean
                    df['givesbonus_acceptspedelecs_fbbattlevel'] = df['givesbonus_acceptspedelecs_fbbattlevel'].fillna('false')
                    df['givesbonus_acceptspedelecs_fbbattlevel'] = df['givesbonus_acceptspedelecs_fbbattlevel'].map({
                        'true': True, 
                        'false': False,
                        '': False,  # Handle empty strings
                        'True': True,
                        'False': False
                    })
                    # Fill any remaining unmapped values with False
                    df['givesbonus_acceptspedelecs_fbbattlevel'] = df['givesbonus_acceptspedelecs_fbbattlevel'].fillna(False)
                    
                    df['pedelecs'] = df['pedelecs'].astype(int)
                    
                    # Move name as 2nd column
                    df = df[['station_id', 'name'] + [col for col in df.columns if col not in ['station_id', 'name']]]
                    
                    context.log.info(f"Successfully processed {len(df)} records from {raw_data_key}")
                    context.log.info(f"Final columns: {list(df.columns)}")
                    
                    # Log the data types to debug
                    context.log.info(f"givesbonus_acceptspedelecs_fbbattlevel dtype: {df['givesbonus_acceptspedelecs_fbbattlevel'].dtype}")
                    context.log.info(f"givesbonus_acceptspedelecs_fbbattlevel unique values: {df['givesbonus_acceptspedelecs_fbbattlevel'].unique()}")
                    
                except Exception as e:
                    context.log.error(f"Failed to convert data types for {raw_data_key}: {e}")
                    continue
                
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
                
                # Validate with Pandera schema before adding to collection
                try:
                    validated_df = raw_csv_schema.validate(df, lazy=True)
                    context.log.info(f"Pandera validation passed for {len(validated_df)} records from {raw_data_key}")
                    
                    # Add additional required columns after validation
                    validated_df['date'] = pd.to_datetime(partition_date)
                    validated_df['processed_at'] = datetime.now()
                    validated_df['s3_source_key'] = raw_data_key
                    
                    all_dataframes.append(validated_df)
                    processed_files.append({
                        "file_key": raw_data_key,
                        "file_timestamp": file_timestamp,
                        "records_count": len(validated_df)
                    })
                    
                    context.log.info(f"Successfully processed and validated {len(validated_df)} records from {raw_data_key}")
                    
                except pa.errors.SchemaErrors as e:
                    context.log.error(f"Pandera validation failed for {raw_data_key}: {e}")
                    
                    # Log detailed validation errors
                    for error in e.failure_cases.itertuples():
                        context.log.error(f"Validation error - Column: {error.column}, Check: {error.check}, Value: {error.failure_case}")
                    
                    # Skip this file entirely if validation fails
                    context.log.warning(f"Skipping file {raw_data_key} due to validation failures")
                    continue
                    
            except Exception as e:
                context.log.error(f"Failed to process file {raw_data_key}: {e}")
                continue
        
        if not all_dataframes:
            context.log.error(f"No valid data found after processing all files for partition {partition_date}")
            context.log.error(f"Processed files: {[f['file_key'] for f in processed_files]}")
            raise ValueError("No valid data found after processing and validation")
        
        # Combine all validated dataframes
        df = pd.concat(all_dataframes, ignore_index=True)
        
        # Use the timestamp from the most recent file for the processed data filename
        latest_file_timestamp = max(processed_files, key=lambda x: x['file_timestamp'])['file_timestamp']
        
        # Log the classification results
        station_count = len(df[df['record_type'] == 'station'])
        bike_count = len(df[df['record_type'] == 'bike'])
        unknown_count = len(df[df['record_type'] == 'unknown'])
        
        context.log.info(f"Classified data for partition {partition_date}: {station_count} stations, {bike_count} bikes, {unknown_count} unknown")
        
        # Generate S3 key and upload
        timestamp = latest_file_timestamp.strftime("%Y%m%d_%H%M%S")
        s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/all/dt={partition_date}/all_processed_{timestamp}.parquet"
        
        # Upload to S3
        buffer = BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        context.log.info(f"Processed data saved to S3: {s3_key}")
        
        # Add metadata
        context.add_output_metadata({
            "columns": list(df.columns),
            "total_records": len(df),
            "station_records": station_count,
            "bike_records": bike_count,
            "unknown_records": unknown_count,
            "partition_date": partition_date,
            "file_timestamp": latest_file_timestamp.isoformat(),
            "processing_timestamp": datetime.now().isoformat(),
            "s3_key": s3_key,
            "processed_files_count": len(processed_files),
            "processed_files": [f['file_key'] for f in processed_files],
            "files_record_counts": {f['file_key']: f['records_count'] for f in processed_files}
        })
        
        return s3_key
        
    except Exception as e:
        context.log.error(f"Failed to validate and process station data for partition {partition_date}: {e}")
        raise


@asset(
    name="wrm_stations_data",
    deps=[wrm_stations_all_processed_asset],
    partitions_def=daily_partitions,
    compute_kind="pandas",
    group_name="wrm_data_processing",
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

@asset(
    name="wrm_bikes_data",
    deps=[wrm_stations_all_processed_asset],
    partitions_def=daily_partitions,
    compute_kind="pandas",
    group_name="wrm_data_processing",
    required_resource_keys={"s3_resource"},
    io_manager_key="iceberg_io_manager",  # Add this to use Iceberg I/O manager
    metadata={
        "partition_expr": "date",
        "partition_spec_update_mode": "update"
    }
)
def wrm_bikes_data_asset(context: AssetExecutionContext) -> pd.DataFrame:
    """Extract and store bike-only data from processed data"""
    
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
                "bike_records": 0
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
        
        # Filter for bike records only based on record_type column
        bikes_df = df[df['record_type'] == 'bike'].copy()
        
        # Drop the record_type column as it's no longer needed
        bikes_df = bikes_df.drop(columns=['record_type'])
        
        if bikes_df.empty:
            context.log.warning(f"No bike records found in processed data for partition {partition_date}")
            
            # Add metadata for empty result
            context.add_output_metadata({
                "partition_date": partition_date,
                "status": "no_bike_records",
                "bike_records": 0
            })
            
            return pd.DataFrame()
        
        context.log.info(f"Filtered to {len(bikes_df)} bike records")
        
        # Convert timestamp columns to microsecond precision for Iceberg compatibility
        datetime_columns = bikes_df.select_dtypes(include=['datetime64[ns]']).columns
        for col in datetime_columns:
            bikes_df[col] = bikes_df[col].astype('datetime64[us]')
        
        # Add metadata
        context.add_output_metadata({
            "bike_records": len(bikes_df),
            "partition_date": partition_date,
            "data_date": bikes_df['date'].iloc[0].isoformat() if not bikes_df.empty else None,
            "processed_files_count": len(all_dataframes)
        })
        
        # The DataFrame will be automatically saved to Iceberg by the I/O manager
        return bikes_df
        
    except Exception as e:
        context.log.error(f"Failed to process bikes data for partition {partition_date}: {e}")
        raise