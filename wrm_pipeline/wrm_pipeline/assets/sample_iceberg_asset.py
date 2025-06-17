# --- SIMPLE EXAMPLE ---

import pandas as pd
from dagster import asset, OpExecutionContext
from datetime import datetime
import random

@asset
def my_table() -> pd.DataFrame:
    # Create pandas DataFrame directly
    df = pd.DataFrame({
        'n_legs': [2, 4, 5, 100],
        'animals': ["Flamingo", "Horse", "Brittle stars", "Centipede"]
    })
    return df


@asset
def my_analysis(my_table: pd.DataFrame) -> pd.DataFrame:
    # Filter using pandas operations
    filtered = my_table[my_table['n_legs'] > 4].copy()
    # Add year column
    filtered['year'] = 2025
    return filtered

# --- ADVANCED EXAMPLE ---

@asset(
    io_manager_key="io_manager",
    key_prefix=["bike_data", "sample"],
    group_name="iceberg_samples"
)
def sample_bike_stations_iceberg(context: OpExecutionContext) -> pd.DataFrame:
    """
    Sample asset that creates mock bike station data and stores it in Iceberg format.
    This demonstrates how to use the Iceberg IO manager with pandas DataFrames.
    """
    context.log.info("Creating sample bike station data for Iceberg storage")
    
    # Create sample data
    num_stations = 50
    station_ids = list(range(1, num_stations + 1))
    station_names = [f"Station_{i:03d}" for i in station_ids]
    latitudes = [51.1 + random.uniform(-0.1, 0.1) for _ in station_ids]
    longitudes = [17.0 + random.uniform(-0.1, 0.1) for _ in station_ids]
    bike_counts = [random.randint(0, 20) for _ in station_ids]
    dock_counts = [random.randint(10, 30) for _ in station_ids]
    timestamps = [datetime.now() for _ in station_ids]
    
    # Create pandas DataFrame
    df = pd.DataFrame({
        'station_id': station_ids,
        'station_name': station_names,
        'latitude': latitudes,
        'longitude': longitudes,
        'available_bikes': bike_counts,
        'available_docks': dock_counts,
        'last_updated': timestamps,
    })
    
    # Ensure proper data types
    df['station_id'] = df['station_id'].astype('int64')
    df['available_bikes'] = df['available_bikes'].astype('int32')
    df['available_docks'] = df['available_docks'].astype('int32')
    df['last_updated'] = pd.to_datetime(df['last_updated'])
    
    context.log.info(f"Created DataFrame with {len(df)} rows and {len(df.columns)} columns")
    context.log.info(f"Columns: {list(df.columns)}")
    
    return df

@asset(
    io_manager_key="io_manager",
    key_prefix=["bike_data", "processed"],
    group_name="iceberg_samples",
    deps=[sample_bike_stations_iceberg]
)
def processed_bike_stations_iceberg(context: OpExecutionContext, sample_bike_stations_iceberg: pd.DataFrame) -> pd.DataFrame:
    """
    Asset that processes the sample bike station data and creates derived metrics.
    Demonstrates reading from and writing to Iceberg tables using pandas DataFrames.
    """
    context.log.info("Processing bike station data")
    
    # Create a copy to avoid modifying the input
    df = sample_bike_stations_iceberg.copy()
    
    # Add calculated columns
    df['total_capacity'] = df['available_bikes'] + df['available_docks']
    df['utilization_rate'] = df['available_bikes'] / df['total_capacity']
    
    # Add processing timestamp
    df['processed_at'] = datetime.now()
    
    context.log.info(f"Processed DataFrame with {len(df)} rows and {len(df.columns)} columns")
    
    return df