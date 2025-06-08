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
        return combined_df
    else:
        return pd.DataFrame()

@asset(
    name="wrm_stations_postgres",
    description="WRM stations data loaded into PostgreSQL",
    deps=[wrm_stations_data],
    compute_kind="postgres",
    group_name="wrm_stations",
    required_resource_keys={"postgres_resource"}  # Add this line
)
def wrm_stations_postgres(context: AssetExecutionContext, wrm_stations_data: pd.DataFrame) -> MaterializeResult:
    """
    Asset that loads WRM stations data into PostgreSQL database.
    """
    if wrm_stations_data.empty:
        context.log.info("No data to load into PostgreSQL")
        return MaterializeResult(
            metadata={
                "records_inserted": MetadataValue.int(0),
                "files_processed": MetadataValue.int(0)
            }
        )
    
    postgres_resource = context.resources.postgres_resource
    records_inserted = 0
    
    with postgres_resource.get_connection() as conn:
        with conn.cursor() as cursor:
            # Prepare insert statement with conflict handling
            insert_query = """
            INSERT INTO wrm_stations (
                station_id, station_name, latitude, longitude, 
                total_docks, available_bikes, available_docks, 
                status, date, last_reported, processed_at, s3_source_key
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_id, date, processed_at) DO NOTHING
            """
            
            # Insert each row
            for _, row in wrm_stations_data.iterrows():
                cursor.execute(insert_query, (
                    row.get('station_id'),
                    row.get('station_name'),
                    row.get('latitude'),
                    row.get('longitude'),
                    row.get('total_docks'),
                    row.get('available_bikes'),
                    row.get('available_docks'),
                    row.get('status'),
                    row.get('date'),
                    row.get('last_reported'),
                    row.get('processed_at'),
                    row.get('s3_source_key')
                ))
            
            conn.commit()
            records_inserted = len(wrm_stations_data)
    
    context.log.info(f"Successfully inserted {records_inserted} records into PostgreSQL")
    
    return MaterializeResult(
        metadata={
            "records_inserted": MetadataValue.int(records_inserted),
            "files_processed": MetadataValue.int(len(wrm_stations_data['s3_source_key'].unique())),
            "latest_data_date": MetadataValue.text(str(wrm_stations_data['date'].max()) if 'date' in wrm_stations_data.columns else "N/A")
        }
    )