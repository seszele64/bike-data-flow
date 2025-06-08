from dagster import asset, AssetExecutionContext, MaterializeResult, MetadataValue
import pandas as pd
import ftfy
from io import StringIO, BytesIO
from datetime import datetime
from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from .stations import wrm_stations_processed_asset
from .stations_deduplicated import s3_processed_stations_list

def process_station_data(context: AssetExecutionContext, s3_client, raw_s3_key: str) -> str:
    """
    Extracted processing logic that can be called from both asset and batch processor.
    """
    try:
        # Download raw data from S3
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=raw_s3_key)
        raw_data = response['Body'].read().decode('utf-8')
        
        # Fix encoding issues
        fixed_data = ftfy.fix_text(raw_data)
        
        # Parse CSV data
        df = pd.read_csv(StringIO(fixed_data))
        
        # Process the data
        df = df.rename(columns={'#id': 'station_id'})
        
        # Split the second column into multiple columns
        second_col_name = df.columns[1]  # Get the name of the second column
        
        # Split the values and create new columns
        split_data = df[second_col_name].str.split('|', expand=True)
        
        # Add the new columns to your dataframe
        df['timestamp'] = pd.to_datetime(split_data[0], unit='s')  # Convert first value to datetime
        df['timezone_1'] = split_data[1].astype(int)   # Second value as int
        df['timezone_2'] = split_data[2].astype(int)   # Third value as int
        
        # Optional: drop the original column if you don't need it anymore
        df = df.drop(columns=[second_col_name])
        
        # move timestamp as 2nd column
        df = df[['station_id', 'timestamp'] + [col for col in df.columns if col not in ['station_id', 'timestamp']]]
        
        # Extract date from the timestamp column
        if not df.empty and 'timestamp' in df.columns:
            data_date = df['timestamp'].iloc[0].strftime('%Y-%m-%d')
        else:
            # Fallback to current date if no timestamp found
            data_date = datetime.now().strftime("%Y-%m-%d")
        
        # Add date and processing timestamp
        df['date'] = data_date
        df['processed_at'] = datetime.now()
        
        # Extract original file's date and timestamp from the raw S3 key
        # Example: bike-data/gen_info/raw/dt=2025-06-07/station_data_20250607_142014.txt
        # Extract date partition from path
        if '/dt=' in raw_s3_key:
            original_date = raw_s3_key.split('/dt=')[1].split('/')[0]  # Gets "2025-06-07"
        else:
            original_date = data_date  # Fallback to data date
        
        # Extract original timestamp from filename
        filename = raw_s3_key.split('/')[-1]  # Gets the filename
        
        # Handle different filename patterns
        if 'station_data_' in filename and '_' in filename:
            # Pattern: station_data_20250607_142014.txt -> 20250607_142014
            timestamp_part = filename.replace('station_data_', '').replace('.txt', '')
            original_timestamp = timestamp_part
        elif 'wrm_stations_' in filename:
            # Pattern: wrm_stations_2025-05-11_18-13-14.txt -> 20250511_181314
            # Extract date and time parts and reformat
            timestamp_part = filename.replace('wrm_stations_', '').replace('.txt', '')
            if '_' in timestamp_part:
                date_part, time_part = timestamp_part.split('_', 1)
                # Convert 2025-05-11 to 20250511
                formatted_date = date_part.replace('-', '')
                # Convert 18-13-14 to 181314
                formatted_time = time_part.replace('-', '')
                original_timestamp = f"{formatted_date}_{formatted_time}"
            else:
                original_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        else:
            # Fallback to current timestamp
            original_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Generate S3 key for processed data using original date and timestamp
        processed_s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/dt={original_date}/stations_{original_timestamp}.parquet"
        
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
        context.log.error(f"Failed to process station data from {raw_s3_key}: {e}")
        raise

@asset(
    name="s3_raw_stations_list",
    description="Lists all raw station files available in S3",
    compute_kind="s3",
    group_name="wrm_stations_raw",
    required_resource_keys={"s3_resource"}
)
def s3_raw_stations_list(context: AssetExecutionContext) -> list:
    """
    Asset that retrieves list of raw station files from S3.
    """
    s3_client = context.resources.s3_resource
    s3_prefix = f"{WRM_STATIONS_S3_PREFIX}raw/"
    
    try:
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=s3_prefix
        )
        
        raw_files = []
        if 'Contents' in response:
            raw_files = [
                obj['Key'] for obj in response['Contents'] 
                if obj['Key'].endswith('.txt')
            ]
        
        # Sort files chronologically
        raw_files.sort()
        
        context.log.info(f"Found {len(raw_files)} raw station files in S3")
        
        return raw_files
        
    except Exception as e:
        context.log.error(f"Error listing S3 raw files: {e}")
        raise

@asset(
    name="wrm_raw_stations_data",
    description="Raw WRM stations data from S3",
    deps=[s3_raw_stations_list],
    compute_kind="pandas",
    group_name="wrm_stations_raw",
    required_resource_keys={"s3_resource"}
)
def wrm_raw_stations_data(context: AssetExecutionContext, s3_raw_stations_list: list) -> pd.DataFrame:
    """
    Asset that loads and combines raw station data from S3 files.
    """
    s3_client = context.resources.s3_resource
    
    if not s3_raw_stations_list:
        context.log.info("No raw files found")
        return pd.DataFrame()
    
    all_dataframes = []
    
    for s3_key in s3_raw_stations_list:
        try:
            # Download raw file from S3
            response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
            raw_data = response['Body'].read().decode('utf-8')
            
            # Parse CSV data (similar to processing logic)
            df = pd.read_csv(StringIO(raw_data))
            df['s3_source_key'] = s3_key  # Add source tracking
            df['file_timestamp'] = s3_key.split('_')[-1].split('.')[0] if '_' in s3_key else None
            
            all_dataframes.append(df)
            
            context.log.info(f"Loaded {len(df)} records from {s3_key}")
            
        except Exception as e:
            context.log.error(f"Error loading raw file {s3_key}: {e}")
            continue
    
    if all_dataframes:
        combined_df = pd.concat(all_dataframes, ignore_index=True)
        context.log.info(f"Combined {len(combined_df)} total records from {len(all_dataframes)} raw files")
        
        return combined_df
    else:
        return pd.DataFrame()

@asset(
    name="wrm_stations_batch_processor",
    description="Processes multiple raw station files through the processing pipeline",
    deps=[wrm_raw_stations_data],
    compute_kind="batch_processing",
    group_name="wrm_stations_raw",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_batch_processor(context: AssetExecutionContext, wrm_raw_stations_data: pd.DataFrame) -> MaterializeResult:
    """
    Asset that triggers processing operations for raw station data files.
    """
    if wrm_raw_stations_data.empty:
        context.log.info("No raw data available for processing")
        return MaterializeResult(
            metadata={
                "files_processed": MetadataValue.int(0),
                "records_processed": MetadataValue.int(0)
            }
        )
    
    s3_client = context.resources.s3_resource
    
    # Get unique source files from the combined data
    source_files = wrm_raw_stations_data['s3_source_key'].unique()
    processed_files = 0
    total_records_processed = 0
    failed_files = []
    
    context.log.info(f"Starting batch processing of {len(source_files)} raw files")
    
    for raw_file_key in source_files:
        try:
            context.log.info(f"Processing raw file: {raw_file_key}")
            
            # Call the extracted processing function
            processed_s3_key = process_station_data(context, s3_client, raw_file_key)
            
            if processed_s3_key:
                processed_files += 1
                
                # Get record count from the file subset
                file_records = len(wrm_raw_stations_data[wrm_raw_stations_data['s3_source_key'] == raw_file_key])
                total_records_processed += file_records
                
                context.log.info(f"Successfully processed {raw_file_key} -> {processed_s3_key}")
            else:
                failed_files.append(raw_file_key)
                context.log.error(f"Processing returned no result for {raw_file_key}")
                
        except Exception as e:
            failed_files.append(raw_file_key)
            context.log.error(f"Failed to process {raw_file_key}: {e}")
            continue
    
    # Log summary
    context.log.info(f"Batch processing completed:")
    context.log.info(f"  - Successfully processed: {processed_files} files")
    context.log.info(f"  - Failed to process: {len(failed_files)} files")
    context.log.info(f"  - Total records processed: {total_records_processed}")
    
    if failed_files:
        context.log.warning(f"Failed files: {failed_files}")
    
    return MaterializeResult(
        metadata={
            "files_processed": MetadataValue.int(processed_files),
            "files_failed": MetadataValue.int(len(failed_files)),
            "total_raw_files": MetadataValue.int(len(source_files)),
            "records_processed": MetadataValue.int(total_records_processed),
            "processing_date": MetadataValue.text(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "failed_files": MetadataValue.text(str(failed_files) if failed_files else "None")
        }
    )

@asset(
    name="wrm_stations_processing_summary",
    description="Summary of raw to processed data pipeline status",
    deps=[wrm_stations_batch_processor],
    compute_kind="summary",
    group_name="wrm_stations_raw",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_processing_summary(context: AssetExecutionContext, wrm_stations_batch_processor: MaterializeResult) -> MaterializeResult:
    """
    Asset that provides summary statistics comparing raw vs processed files.
    """
    s3_client = context.resources.s3_resource
    
    try:
        # Count raw files
        raw_response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=f"{WRM_STATIONS_S3_PREFIX}raw/"
        )
        raw_file_count = len([obj for obj in raw_response.get('Contents', []) if obj['Key'].endswith('.txt')])
        
        # Count processed files
        processed_response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=f"{WRM_STATIONS_S3_PREFIX}processed/"
        )
        processed_file_count = len([obj for obj in processed_response.get('Contents', []) if obj['Key'].endswith('.parquet')])
        
        # Calculate processing ratio
        processing_ratio = processed_file_count / raw_file_count if raw_file_count > 0 else 0
        
        context.log.info(f"Processing Pipeline Summary:")
        context.log.info(f"  - Raw files in S3: {raw_file_count}")
        context.log.info(f"  - Processed files in S3: {processed_file_count}")
        context.log.info(f"  - Processing ratio: {processing_ratio:.2%}")
        
        return MaterializeResult(
            metadata={
                "raw_files_count": MetadataValue.int(raw_file_count),
                "processed_files_count": MetadataValue.int(processed_file_count),
                "processing_ratio": MetadataValue.float(processing_ratio),
                "summary_generated_at": MetadataValue.text(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "batch_processor_metadata": MetadataValue.text(str(wrm_stations_batch_processor.metadata) if wrm_stations_batch_processor.metadata else "No metadata")
            }
        )
        
    except Exception as e:
        context.log.error(f"Error generating processing summary: {e}")
        raise