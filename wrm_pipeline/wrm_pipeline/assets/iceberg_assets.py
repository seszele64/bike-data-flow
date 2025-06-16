from dagster import asset, AssetExecutionContext, DailyPartitionsDefinition
import pandas as pd
from datetime import datetime
from io import BytesIO
from ..resources import iceberg_io_manager, s3_resource
from ..config import BUCKET_NAME

# Use the same partitioning as your existing assets
daily_partitions = DailyPartitionsDefinition(start_date="2025-05-01")

@asset(
    partitions_def=daily_partitions,
    group_name="iceberg_persistence",
    required_resource_keys={"s3_resource"},
    metadata={"partition_expr": "partition_date"},
    io_manager_key="iceberg_io_manager"  # Add this to use Iceberg I/O manager
)
def stations_iceberg_table(
    context: AssetExecutionContext, 
    wrm_stations_data: pd.DataFrame
) -> pd.DataFrame:
    """
    Persist station data to Iceberg table for long-term storage and querying.
    
    This asset takes the processed station data and stores it in an Iceberg table
    with proper partitioning and schema evolution support.
    """
    # Handle empty result from upstream asset
    if wrm_stations_data.empty:
        context.log.info(f"No station data available for {context.partition_key}")
        return pd.DataFrame()
    
    # Work directly with the DataFrame from upstream asset
    stations_df = wrm_stations_data.copy()
    
    # Add partition date column directly as datetime
    stations_df['partition_date'] = pd.to_datetime(context.partition_key)
    
    # Convert timestamp columns to microsecond precision for Iceberg compatibility
    datetime_columns = stations_df.select_dtypes(include=['datetime64[ns]']).columns
    for col in datetime_columns:
        stations_df[col] = stations_df[col].astype('datetime64[us]')
    
    context.log.info(f"Persisting {len(stations_df)} station records to Iceberg table for {context.partition_key}")
    context.log.info(f"partition_date column type: {stations_df['partition_date'].dtype}")
    context.log.info(f"partition_date sample value: {stations_df['partition_date'].iloc[0]}")
    
    # The DataFrame will be automatically saved to Iceberg by the I/O manager
    return stations_df

@asset(
    partitions_def=daily_partitions,
    group_name="iceberg_persistence",
    required_resource_keys={"s3_resource"},
    metadata={"partition_expr": "partition_date"}
)
def bikes_iceberg_table(
    context: AssetExecutionContext, 
    wrm_bikes_data: pd.DataFrame
) -> pd.DataFrame:
    """
    Persist bike data to Iceberg table for long-term storage and querying.
    
    This asset takes the processed bike data and stores it in an Iceberg table
    with proper partitioning and schema evolution support.
    """
    # Get S3 client
    s3_client = context.resources.s3_resource
    
    # Handle empty result from upstream asset
    if wrm_bikes_data.empty:
        context.log.info(f"No bike data available for {context.partition_key}")
        return pd.DataFrame()
    
    # Load data from S3
    try:
        file_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_bikes_data)
        bikes_df = pd.read_parquet(BytesIO(file_response['Body'].read()))
        
        # Add partition date column directly as datetime
        bikes_df['partition_date'] = pd.to_datetime(context.partition_key)
        
        # Convert timestamp columns to microsecond precision for Iceberg compatibility
        datetime_columns = bikes_df.select_dtypes(include=['datetime64[ns]']).columns
        for col in datetime_columns:
            bikes_df[col] = bikes_df[col].astype('datetime64[us]')
        
        context.log.info(f"Persisting {len(bikes_df)} bike records to Iceberg table for {context.partition_key}")
        
        return bikes_df
        
    except Exception as e:
        context.log.error(f"Failed to load bike data from S3 key {wrm_bikes_data}: {e}")
        raise

@asset(
    partitions_def=daily_partitions,
    group_name="iceberg_persistence",
    required_resource_keys={"s3_resource"},
    metadata={"partition_expr": "partition_date"}
)
def all_processed_iceberg_table(
    context: AssetExecutionContext, 
    wrm_stations_all_processed: str  # This is the S3 key, not a DataFrame
) -> pd.DataFrame:
    """
    Persist all processed data (stations + bikes + unknown) to Iceberg table.
    
    This comprehensive table includes all record types with the record_type classification,
    useful for data analysis and debugging.
    """
    # Get S3 client
    s3_client = context.resources.s3_resource
    
    # Handle empty result from upstream asset
    if not wrm_stations_all_processed:
        context.log.info(f"No processed data available for {context.partition_key}")
        return pd.DataFrame()
    
    # Load data from S3
    try:
        file_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_stations_all_processed)
        all_data_df = pd.read_parquet(BytesIO(file_response['Body'].read()))
        
        # Add partition date column directly as datetime
        all_data_df['partition_date'] = pd.to_datetime(context.partition_key)
        
        # Convert timestamp columns to microsecond precision for Iceberg compatibility
        datetime_columns = all_data_df.select_dtypes(include=['datetime64[ns]']).columns
        for col in datetime_columns:
            all_data_df[col] = all_data_df[col].astype('datetime64[us]')
        
        context.log.info(f"Persisting {len(all_data_df)} total records to comprehensive Iceberg table for {context.partition_key}")
        
        return all_data_df
        
    except Exception as e:
        context.log.error(f"Failed to load processed data from S3 key {wrm_stations_all_processed}: {e}")
        raise

# Optional: Create aggregated/summary tables
@asset(
    partitions_def=daily_partitions,
    group_name="iceberg_analytics"
)
def daily_station_summary(
    context: AssetExecutionContext,
    stations_iceberg_table: pd.DataFrame
) -> pd.DataFrame:
    """
    Create daily summary statistics for stations.
    
    Provides aggregated metrics like available bikes, docks, etc. per station per day.
    """
    if stations_iceberg_table.empty:
        context.log.info(f"No station data for {context.partition_key}")
        return pd.DataFrame()
    
    summary_df = stations_iceberg_table.groupby(['station_id', 'name']).agg({
        'bikes': ['mean', 'max', 'min', 'std'],
        'spaces': ['mean', 'max', 'min', 'std'],  # Changed from 'free' to 'spaces'
        'total_docks': 'first',  # Changed from 'total'
        'installed': lambda x: x.sum() / len(x),  # Changed from 'on'
        'partition_date': 'first'
    }).round(2)
    
    # Flatten column names
    summary_df.columns = ['_'.join(col).strip() if col[1] else col[0] for col in summary_df.columns]
    summary_df = summary_df.reset_index()
    
    context.log.info(f"Created daily summary for {len(summary_df)} unique stations on {context.partition_key}")
    
    return summary_df

@asset(
    partitions_def=daily_partitions,
    group_name="iceberg_analytics"
)
def daily_bike_summary(
    context: AssetExecutionContext,
    bikes_iceberg_table: pd.DataFrame
) -> pd.DataFrame:
    """
    Create daily summary statistics for bikes.
    
    Provides tracking and status information for individual bikes.
    """
    if bikes_iceberg_table.empty:
        context.log.info(f"No bike data for {context.partition_key}")
        return pd.DataFrame()
    
    summary_df = bikes_iceberg_table.groupby(['station_id', 'name']).agg({  # Changed from 'id'
        'lat': ['first', 'last', 'std'],  # track movement
        'lon': ['first', 'last', 'std'],  # Changed from 'lng'
        'installed': lambda x: x.sum() / len(x),  # Changed from 'on'
        'partition_date': 'first'
    }).round(6)
    
    # Flatten column names
    summary_df.columns = ['_'.join(col).strip() if col[1] else col[0] for col in summary_df.columns]
    summary_df = summary_df.reset_index()
    
    context.log.info(f"Created daily summary for {len(summary_df)} unique bikes on {context.partition_key}")
    
    return summary_df