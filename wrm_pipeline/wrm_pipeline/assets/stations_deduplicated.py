from dagster import asset, AssetExecutionContext, MaterializeResult, MetadataValue, DailyPartitionsDefinition
import pandas as pd
import duckdb
from io import BytesIO
from datetime import datetime, timedelta
from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

# Define daily partitions starting from when your data begins
daily_partitions = DailyPartitionsDefinition(start_date="2025-05-10")

@asset(
    name="s3_processed_stations_list",
    description="Lists all processed station files available in S3",
    compute_kind="s3",
    group_name="wrm_stations",
    required_resource_keys={"s3_resource"}
)
def s3_processed_stations_list(context: AssetExecutionContext) -> list:
    """
    Asset that retrieves list of processed station files from S3.
    """
    s3_client = context.resources.s3_resource
    s3_prefix = f"{WRM_STATIONS_S3_PREFIX}processed/"
    
    try:
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=s3_prefix
        )
        
        parquet_files = []
        if 'Contents' in response:
            parquet_files = [
                obj['Key'] for obj in response['Contents'] 
                if obj['Key'].endswith('.parquet')
            ]
        
        # Sort files chronologically
        parquet_files.sort()
        
        context.log.info(f"Found {len(parquet_files)} processed station files in S3")
        
        return parquet_files
        
    except Exception as e:
        context.log.error(f"Error listing S3 files: {e}")
        raise

@asset(
    name="wrm_stations_daily_deduplicated",
    description="Daily deduplicated WRM stations data using efficient DuckDB processing",
    partitions_def=daily_partitions,
    deps=[s3_processed_stations_list],
    compute_kind="duckdb",
    group_name="wrm_stations",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_daily_deduplicated(
    context: AssetExecutionContext, 
    s3_processed_stations_list: list
) -> pd.DataFrame:
    """
    Partitioned asset that processes and deduplicates station data for a specific date.
    Uses DuckDB for efficient deduplication operations.
    """
    s3_client = context.resources.s3_resource
    partition_date = context.partition_key
    partition_datetime = datetime.strptime(partition_date, "%Y-%m-%d")
    
    context.log.info(f"Processing deduplication for partition: {partition_date}")
    
    if not s3_processed_stations_list:
        context.log.info("No processed files found")
        return pd.DataFrame()
    
    # Filter files that match the partition date (assuming files have date in their names)
    relevant_files = [
        file for file in s3_processed_stations_list 
        if partition_date in file or should_process_file_for_date(file, partition_datetime)
    ]
    
    if not relevant_files:
        context.log.info(f"No files found for partition date {partition_date}")
        return pd.DataFrame()
    
    # Initialize DuckDB connection
    conn = duckdb.connect()
    
    try:
        # Load data from relevant S3 files into DuckDB
        all_dataframes = []
        
        for s3_key in relevant_files:
            try:
                # Download parquet file from S3
                response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
                parquet_data = response['Body'].read()
                
                # Read parquet data into DataFrame
                df = pd.read_parquet(BytesIO(parquet_data))
                
                # Filter data for the specific partition date if timestamp column exists
                if 'timestamp' in df.columns:
                    df['date'] = pd.to_datetime(df['timestamp']).dt.date
                    df = df[df['date'] == partition_datetime.date()]
                
                if not df.empty:
                    df['s3_source_key'] = s3_key
                    all_dataframes.append(df)
                    context.log.info(f"Loaded {len(df)} records from {s3_key} for {partition_date}")
                
            except Exception as e:
                context.log.error(f"Error loading file {s3_key}: {e}")
                continue
        
        if not all_dataframes:
            context.log.info(f"No data found for partition {partition_date}")
            return pd.DataFrame()
        
        # Combine all dataframes
        combined_df = pd.concat(all_dataframes, ignore_index=True)
        
        # Register DataFrame with DuckDB for efficient deduplication
        conn.register('raw_data', combined_df)
        
        # Perform efficient deduplication using DuckDB
        # Deduplicate by station_id and timestamp combination
        deduplicated_query = """
        SELECT DISTINCT *
        FROM raw_data
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY station_id, timestamp 
            ORDER BY processed_at DESC
        ) = 1
        ORDER BY timestamp DESC, station_id
        """
        
        deduplicated_df = conn.execute(deduplicated_query).df()
        
        context.log.info(f"Deduplicated from {len(combined_df)} to {len(deduplicated_df)} records for {partition_date}")
        
        # Save partitioned deduplicated data to S3
        try:
            # Convert DataFrame to parquet bytes
            parquet_buffer = BytesIO()
            deduplicated_df.to_parquet(parquet_buffer, index=False)
            parquet_data = parquet_buffer.getvalue()
            
            # Upload to S3 with partition-specific path
            s3_output_key = f"bike-data/gen_info/deduplicated/daily/{partition_date}/deduplicated_bike_stations.parquet"
            s3_client.put_object(
                Bucket=BUCKET_NAME,
                Key=s3_output_key,
                Body=parquet_data,
                ContentType='application/octet-stream'
            )
            
            context.log.info(f"Successfully saved deduplicated data to S3: {s3_output_key}")
            
        except Exception as e:
            context.log.error(f"Error saving deduplicated data to S3: {e}")
            # Don't raise - still return the DataFrame even if S3 save fails
        
        return deduplicated_df
        
    finally:
        conn.close()

def should_process_file_for_date(file_path: str, target_date: datetime) -> bool:
    """
    Helper function to determine if a file should be processed for a given date.
    Implement logic based on your file naming convention.
    """
    # Example: if files contain date patterns like "2024-01-01"
    date_str = target_date.strftime("%Y-%m-%d")
    return date_str in file_path

