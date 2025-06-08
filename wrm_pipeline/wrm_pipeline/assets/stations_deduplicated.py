from dagster import asset, AssetExecutionContext, MaterializeResult, MetadataValue
import pandas as pd
from io import BytesIO
from datetime import datetime
from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

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
    name="wrm_stations_data",
    description="Processed WRM stations data from S3",
    deps=[s3_processed_stations_list],
    compute_kind="pandas",
    group_name="wrm_stations",
    required_resource_keys={"s3_resource"}  # Add this line
)
def wrm_stations_data(context: AssetExecutionContext, s3_processed_stations_list: list) -> pd.DataFrame:
    """
    Asset that loads and combines processed station data from S3 files.
    """
    s3_client = context.resources.s3_resource
    
    if not s3_processed_stations_list:
        context.log.info("No processed files found")
        return pd.DataFrame()
    
    all_dataframes = []
    
    for s3_key in s3_processed_stations_list:
        try:
            # Download parquet file from S3
            response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
            parquet_data = response['Body'].read()
            
            # Read parquet data into DataFrame
            df = pd.read_parquet(BytesIO(parquet_data))
            df['s3_source_key'] = s3_key  # Add source tracking
            all_dataframes.append(df)
            
            context.log.info(f"Loaded {len(df)} records from {s3_key}")
            
        except Exception as e:
            context.log.error(f"Error loading file {s3_key}: {e}")
            continue
    
    if all_dataframes:
        combined_df = pd.concat(all_dataframes, ignore_index=True)
        context.log.info(f"Combined {len(combined_df)} total records from {len(all_dataframes)} files")
        
        # Save combined data to S3 as deduplicated parquet file
        try:
            # Convert DataFrame to parquet bytes
            parquet_buffer = BytesIO()
            combined_df.to_parquet(parquet_buffer, index=False)
            parquet_data = parquet_buffer.getvalue()
            
            # Upload to S3
            s3_output_key = "bike-data/gen_info/deduplicated/deduplicated_bike_stations.parquet"
            s3_client.put_object(
                Bucket=BUCKET_NAME,
                Key=s3_output_key,
                Body=parquet_data,
                ContentType='application/octet-stream'
            )
            
            context.log.info(f"Successfully saved deduplicated data to S3: {s3_output_key}")
            context.log.info(f"Saved {len(combined_df)} records to deduplicated file")
            
        except Exception as e:
            context.log.error(f"Error saving deduplicated data to S3: {e}")
            # Don't raise - still return the DataFrame even if S3 save fails
        
        return combined_df
    else:
        return pd.DataFrame()