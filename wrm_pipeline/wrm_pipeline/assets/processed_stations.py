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

@asset(
    name="wrm_stations_postgres",
    description="WRM stations data loaded into PostgreSQL",
    deps=[wrm_stations_data],
    compute_kind="postgres",
    group_name="wrm_stations",
    required_resource_keys={"postgres_resource"}
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
    
    # Log data quality information
    context.log.info(f"DataFrame shape: {wrm_stations_data.shape}")
    context.log.info(f"DataFrame columns: {list(wrm_stations_data.columns)}")
    context.log.info(f"DataFrame dtypes:\n{wrm_stations_data.dtypes}")
    
    # Check for problematic values
    for col in wrm_stations_data.columns:
        null_count = wrm_stations_data[col].isnull().sum()
        if null_count > 0:
            context.log.info(f"Column '{col}' has {null_count} null values")
        
        # Check for NaT values in datetime columns
        if wrm_stations_data[col].dtype.name.startswith('datetime'):
            nat_count = pd.isna(wrm_stations_data[col]).sum()
            context.log.info(f"Column '{col}' has {nat_count} NaT values")
    
    # Clean the data before insertion
    cleaned_data = wrm_stations_data.copy()
    
    # Replace NaT values with None for datetime columns
    datetime_columns = cleaned_data.select_dtypes(include=['datetime64']).columns
    for col in datetime_columns:
        cleaned_data[col] = cleaned_data[col].where(pd.notna(cleaned_data[col]), None)
        context.log.info(f"Cleaned NaT values in column '{col}'")
    
    # Replace NaN values with None for numeric columns
    numeric_columns = cleaned_data.select_dtypes(include=['float64', 'int64']).columns
    for col in numeric_columns:
        cleaned_data[col] = cleaned_data[col].where(pd.notna(cleaned_data[col]), None)
    
    postgres_resource = context.resources.postgres_resource
    records_inserted = 0
    failed_inserts = 0
    
    with postgres_resource.get_connection() as conn:
        # Set autocommit to handle each insert separately
        conn.autocommit = True
        
        with conn.cursor() as cursor:
            # Check what columns actually exist in the table
            cursor.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'wrm_stations' 
                ORDER BY ordinal_position;
            """)
            
            existing_columns = cursor.fetchall()
            context.log.info(f"Existing table columns: {existing_columns}")
            
            # Prepare insert statement - removed conflict handling since no unique constraint
            insert_query = """
            INSERT INTO wrm_stations (
                station_id, station_name, timestamp, latitude, longitude, 
                total_docks, available_bikes, available_docks, installed, 
                locked, temporary, pedelecs, gmt_local_diff_sec, 
                gmt_servertime_diff_sec, givesbonus_acceptspedelecs_fbbattlevel,
                date, last_reported, processed_at, s3_source_key
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            # Insert each row with error handling
            for idx, row in cleaned_data.iterrows():
                try:
                    # Extract date from timestamp if available
                    date_val = None
                    timestamp_val = row.get('timestamp')
                    
                    # More thorough NaT/None checking for timestamp
                    if timestamp_val is None or pd.isna(timestamp_val) or str(timestamp_val) == 'NaT':
                        timestamp_val = None
                    else:
                        try:
                            # Ensure it's a proper timestamp
                            if isinstance(timestamp_val, str):
                                timestamp_val = pd.to_datetime(timestamp_val)
                            # Extract date for date column
                            if timestamp_val:
                                date_val = timestamp_val.date()
                        except:
                            timestamp_val = None
                    
                    # Use timestamp as last_reported if no separate field
                    last_reported_val = timestamp_val
                    
                    # Set processed_at to current time if not available
                    processed_at_val = row.get('processed_at')
                    if processed_at_val is None or pd.isna(processed_at_val) or str(processed_at_val) == 'NaT':
                        processed_at_val = datetime.now()
                    
                    # Handle string values that might be coming from gmt fields
                    gmt_local_val = row.get('gmt_local_diff_sec')
                    if isinstance(gmt_local_val, str):
                        try:
                            gmt_local_val = int(gmt_local_val)
                        except (ValueError, TypeError):
                            gmt_local_val = None
                    elif pd.isna(gmt_local_val):
                        gmt_local_val = None
                    
                    gmt_server_val = row.get('gmt_servertime_diff_sec')
                    if isinstance(gmt_server_val, str):
                        try:
                            gmt_server_val = int(gmt_server_val)
                        except (ValueError, TypeError):
                            gmt_server_val = None
                    elif pd.isna(gmt_server_val):
                        gmt_server_val = None
                    
                    # Clean all values before insertion
                    def clean_value(val):
                        if val is None or pd.isna(val) or str(val) in ['NaT', 'NaN', 'nan']:
                            return None
                        return val
                    
                    # Skip rows where essential fields are missing
                    station_id = clean_value(row.get('id'))
                    if station_id is None:
                        context.log.debug(f"Skipping row {idx}: missing station_id")
                        continue
                    
                    values = (
                        station_id,                                           # station_id
                        clean_value(row.get('name')),                         # station_name
                        timestamp_val,                                        # timestamp
                        clean_value(row.get('lat')),                          # latitude
                        clean_value(row.get('lon')),                          # longitude
                        clean_value(row.get('total_docks')),                  # total_docks
                        clean_value(row.get('bikes')),                        # available_bikes
                        clean_value(row.get('spaces')),                       # available_docks
                        clean_value(row.get('installed')),                    # installed
                        clean_value(row.get('locked')),                       # locked
                        clean_value(row.get('temporary')),                    # temporary
                        clean_value(row.get('pedelecs')),                     # pedelecs
                        gmt_local_val,                                        # gmt_local_diff_sec
                        gmt_server_val,                                       # gmt_servertime_diff_sec
                        clean_value(row.get('givesbonus_acceptspedelecs_fbbattlevel')),  # givesbonus_acceptspedelecs_fbbattlevel
                        date_val,                                             # date
                        last_reported_val,                                    # last_reported
                        processed_at_val,                                     # processed_at
                        clean_value(row.get('s3_source_key'))                 # s3_source_key
                    )
                    
                    cursor.execute(insert_query, values)
                    records_inserted += 1
                    
                    # Log progress every 1000 records
                    if records_inserted % 1000 == 0:
                        context.log.info(f"Inserted {records_inserted} records so far...")
                    
                except Exception as e:
                    failed_inserts += 1
                    context.log.error(f"Failed to insert row {idx}: {e}")
                    
                    # Only log details for first few failures to avoid spam
                    if failed_inserts <= 5:
                        context.log.error(f"Row data: {dict(row)}")
                        context.log.error(f"Values tuple: {values}")
                    
                    # Continue with next row instead of aborting transaction
                    continue
    
    context.log.info(f"Successfully inserted {records_inserted} records into PostgreSQL")
    if failed_inserts > 0:
        context.log.warning(f"Failed to insert {failed_inserts} records")
    
    return MaterializeResult(
        metadata={
            "records_inserted": MetadataValue.int(records_inserted),
            "failed_inserts": MetadataValue.int(failed_inserts),
            "files_processed": MetadataValue.int(len(cleaned_data['s3_source_key'].unique())),
            "latest_data_date": MetadataValue.text(str(cleaned_data['date'].max()) if 'date' in cleaned_data.columns else "N/A")
        }
    )