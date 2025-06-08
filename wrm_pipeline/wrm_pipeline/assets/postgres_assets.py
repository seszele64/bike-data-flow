from dagster import asset, AssetExecutionContext, DailyPartitionsDefinition
import psycopg2
import pandas as pd
from sqlalchemy import create_engine, text
import uuid
from datetime import datetime
from ..config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD
)

# Define partitions to match your deduplicated asset
daily_partitions = DailyPartitionsDefinition(start_date="2025-05-10")

@asset(
    group_name="database",
    compute_kind="postgresql"
)
def postgres_connection(context: AssetExecutionContext):
    """
    Creates and tests PostgreSQL database connection.
    Returns connection parameters for other assets to use.
    """
    try:
        # Test connection
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        
        # Test query
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        version = cursor.fetchone()
        
        context.log.info(f"Successfully connected to PostgreSQL: {version[0]}")
        
        cursor.close()
        conn.close()
        
        return {
            "host": POSTGRES_HOST,
            "port": POSTGRES_PORT,
            "database": POSTGRES_DB,
            "user": POSTGRES_USER,
            "status": "connected"
        }
        
    except Exception as e:
        context.log.error(f"Failed to connect to PostgreSQL: {str(e)}")
        raise

@asset(
    deps=[postgres_connection],
    group_name="database",
    compute_kind="postgresql"
)
def bike_stations_table(context: AssetExecutionContext):
    """
    Creates bike_stations table in PostgreSQL if it doesn't exist.
    """
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        
        cursor = conn.cursor()
        
        # Create table if not exists
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS bike_stations (
            station_id VARCHAR(100) NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            name VARCHAR(200),
            lat DECIMAL(10, 8),
            lon DECIMAL(11, 8),
            bikes INTEGER,
            spaces INTEGER,
            installed BOOLEAN,
            locked BOOLEAN,
            temporary BOOLEAN,
            total_docks INTEGER,
            givesbonus_acceptspedelecs_fbbattlevel TEXT,
            pedelecs INTEGER,
            timezone_1 INTEGER,
            timezone_2 INTEGER,
            date VARCHAR(50),
            processed_at TIMESTAMP,
            s3_source_key VARCHAR(500),
            PRIMARY KEY (station_id, timestamp)
        );
        """
        
        cursor.execute(create_table_sql)
        conn.commit()
        
        # Get table info
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.tables 
            WHERE table_name = 'bike_stations'
        """)
        table_exists = cursor.fetchone()[0]
        
        context.log.info(f"bike_stations table created/verified: {table_exists > 0}")
        
        cursor.close()
        conn.close()
        
        return {
            "table_name": "bike_stations",
            "status": "ready",
            "exists": table_exists > 0
        }
        
    except Exception as e:
        context.log.error(f"Failed to create bike_stations table: {str(e)}")
        raise

@asset(
    deps=[postgres_connection],
    group_name="database", 
    compute_kind="postgresql"
)
def bike_failures_table(context: AssetExecutionContext):
    """
    Creates bike_failures table in PostgreSQL if it doesn't exist.
    """
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        
        cursor = conn.cursor()
        
        # Create table if not exists
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS bike_failures (
            id SERIAL PRIMARY KEY,
            failure_id VARCHAR(100) UNIQUE,
            station_id VARCHAR(50),
            failure_type VARCHAR(100),
            description TEXT,
            reported_date TIMESTAMP,
            resolved_date TIMESTAMP,
            status VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        
        cursor.execute(create_table_sql)
        conn.commit()
        
        # Get table info
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.tables 
            WHERE table_name = 'bike_failures'
        """)
        table_exists = cursor.fetchone()[0]
        
        context.log.info(f"bike_failures table created/verified: {table_exists > 0}")
        
        cursor.close()
        conn.close()
        
        return {
            "table_name": "bike_failures",
            "status": "ready", 
            "exists": table_exists > 0
        }
        
    except Exception as e:
        context.log.error(f"Failed to create bike_failures table: {str(e)}")
        raise

@asset(
    deps=[bike_stations_table, "wrm_stations_daily_deduplicated"],
    partitions_def=daily_partitions,  # Make this asset partitioned
    group_name="database",
    compute_kind="postgresql",
    required_resource_keys={"s3_resource"}
)
def load_stations_to_postgres(context: AssetExecutionContext, wrm_stations_daily_deduplicated: pd.DataFrame):
    """
    Partition-aware loading of station data using efficient upsert pattern.
    Each partition loads incrementally with conflict resolution.
    """
    partition_date = context.partition_key
    context.log.info(f"Processing partition: {partition_date}")
    
    if wrm_stations_daily_deduplicated.empty:
        context.log.info(f"No station data for partition {partition_date}")
        return {"partition": partition_date, "rows_inserted": 0, "rows_updated": 0}
    
    try:
        # Create SQLAlchemy engine
        engine = create_engine(
            f'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}',
            pool_pre_ping=True,
            pool_recycle=300
        )
        
        # Preprocess data with partition context
        processed_df = preprocess_dataframe_partitioned(wrm_stations_daily_deduplicated, context, partition_date)
        
        if processed_df.empty:
            context.log.warning(f"No valid records after preprocessing for partition {partition_date}")
            return {"partition": partition_date, "rows_inserted": 0, "rows_updated": 0}
        
        # Check existing data for this partition
        existing_stats = get_partition_stats(engine, partition_date, context)
        
        # Use partition-aware upsert
        upsert_result = partition_aware_upsert(engine, processed_df, partition_date, context)
        
        # Log partition completion
        context.log.info(f"Partition {partition_date} completed: {upsert_result}")
        
        engine.dispose()
        
        return {
            "partition": partition_date,
            "rows_inserted": upsert_result["rows_inserted"],
            "rows_updated": upsert_result["rows_updated"],
            "total_processed": len(processed_df),
            "existing_before": existing_stats["count"],
            "status": "completed"
        }
        
    except Exception as e:
        context.log.error(f"Failed to load partition {partition_date}: {str(e)}")
        raise

def get_partition_stats(engine, partition_date, context):
    """
    Get existing statistics for a specific partition date.
    """
    with engine.connect() as conn:
        # Count existing records for this partition date
        result = conn.execute(text("""
            SELECT COUNT(*) as count,
                   MIN(timestamp) as min_timestamp,
                   MAX(timestamp) as max_timestamp
            FROM bike_stations 
            WHERE DATE(timestamp) = :partition_date
        """), {"partition_date": partition_date})
        
        stats = result.fetchone()
        
        return {
            "count": stats[0] if stats else 0,
            "min_timestamp": stats[1] if stats else None,
            "max_timestamp": stats[2] if stats else None
        }

def partition_aware_upsert(engine, df, partition_date, context):
    """
    Efficient partition-aware upsert using PostgreSQL's ON CONFLICT DO UPDATE.
    This pattern is optimized for time-partitioned data.
    """
    staging_table = f"temp_partition_{partition_date.replace('-', '_')}_{uuid.uuid4().hex[:8]}"
    
    try:
        with engine.begin() as conn:
            # Create staging table
            conn.execute(text(f"""
                CREATE TEMP TABLE {staging_table} (
                    station_id VARCHAR(100) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    name VARCHAR(200),
                    lat DECIMAL(10, 8),
                    lon DECIMAL(11, 8),
                    bikes INTEGER,
                    spaces INTEGER,
                    installed BOOLEAN,
                    locked BOOLEAN,
                    temporary BOOLEAN,
                    total_docks INTEGER,
                    givesbonus_acceptspedelecs_fbbattlevel TEXT,
                    pedelecs INTEGER,
                    timezone_1 INTEGER,
                    timezone_2 INTEGER,
                    date VARCHAR(50),
                    processed_at TIMESTAMP,
                    s3_source_key VARCHAR(500)
                )
            """))
            
            # Load data into staging table in chunks
            chunk_size = 5000
            for i in range(0, len(df), chunk_size):
                chunk = df.iloc[i:i+chunk_size]
                chunk.to_sql(
                    name=staging_table,
                    con=conn,
                    if_exists='append',
                    index=False,
                    method='multi'
                )
            
            # Get counts before upsert
            before_count = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
            partition_before = conn.execute(text("""
                SELECT COUNT(*) FROM bike_stations 
                WHERE DATE(timestamp) = :partition_date
            """), {"partition_date": partition_date}).fetchone()[0]
            
            # Perform partition-aware upsert with detailed conflict resolution
            upsert_result = conn.execute(text(f"""
                INSERT INTO bike_stations
                SELECT * FROM {staging_table}
                ON CONFLICT (station_id, timestamp) 
                DO UPDATE SET
                    name = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.name 
                        ELSE bike_stations.name 
                    END,
                    lat = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.lat 
                        ELSE bike_stations.lat 
                    END,
                    lon = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.lon 
                        ELSE bike_stations.lon 
                    END,
                    bikes = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.bikes 
                        ELSE bike_stations.bikes 
                    END,
                    spaces = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.spaces 
                        ELSE bike_stations.spaces 
                    END,
                    installed = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.installed 
                        ELSE bike_stations.installed 
                    END,
                    locked = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.locked 
                        ELSE bike_stations.locked 
                    END,
                    temporary = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.temporary 
                        ELSE bike_stations.temporary 
                    END,
                    total_docks = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.total_docks 
                        ELSE bike_stations.total_docks 
                    END,
                    givesbonus_acceptspedelecs_fbbattlevel = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.givesbonus_acceptspedelecs_fbbattlevel 
                        ELSE bike_stations.givesbonus_acceptspedelecs_fbbattlevel 
                    END,
                    pedelecs = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.pedelecs 
                        ELSE bike_stations.pedelecs 
                    END,
                    timezone_1 = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.timezone_1 
                        ELSE bike_stations.timezone_1 
                    END,
                    timezone_2 = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.timezone_2 
                        ELSE bike_stations.timezone_2 
                    END,
                    date = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.date 
                        ELSE bike_stations.date 
                    END,
                    processed_at = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.processed_at 
                        ELSE bike_stations.processed_at 
                    END,
                    s3_source_key = CASE 
                        WHEN EXCLUDED.processed_at > bike_stations.processed_at 
                        THEN EXCLUDED.s3_source_key 
                        ELSE bike_stations.s3_source_key 
                    END
                WHERE EXCLUDED.processed_at > bike_stations.processed_at
            """))
            
            # Calculate results
            after_count = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
            partition_after = conn.execute(text("""
                SELECT COUNT(*) FROM bike_stations 
                WHERE DATE(timestamp) = :partition_date
            """), {"partition_date": partition_date}).fetchone()[0]
            
            rows_inserted = after_count - before_count
            rows_updated = len(df) - rows_inserted if rows_inserted < len(df) else 0
            
            context.log.info(f"Partition {partition_date}: {rows_inserted} inserted, {rows_updated} updated")
            context.log.info(f"Partition records: {partition_before} → {partition_after}")
            
            return {
                "rows_inserted": rows_inserted,
                "rows_updated": rows_updated,
                "partition_records_before": partition_before,
                "partition_records_after": partition_after
            }
            
    except Exception as e:
        context.log.error(f"Partition-aware upsert failed for {partition_date}: {str(e)}")
        raise

def preprocess_dataframe_partitioned(df, context, partition_date):
    """
    Enhanced preprocessing with partition-aware validation.
    """
    processed_df = df.copy()
    
    # Validate partition date consistency
    if 'timestamp' in processed_df.columns:
        processed_df['timestamp'] = pd.to_datetime(processed_df['timestamp'], utc=True).dt.tz_convert(None)
        
        # Filter for partition date and validate
        partition_datetime = pd.to_datetime(partition_date).date()
        df_dates = processed_df['timestamp'].dt.date
        
        # Check for date mismatches
        date_mismatches = (df_dates != partition_datetime).sum()
        if date_mismatches > 0:
            context.log.warning(f"Found {date_mismatches} records not matching partition date {partition_date}")
            # Filter to only include partition date records
            processed_df = processed_df[df_dates == partition_datetime]
    
    # Enhanced data type handling
    processed_df['station_id'] = processed_df['station_id'].astype('string').str.strip().str.slice(0, 100)
    
    # Remove empty station IDs
    processed_df = processed_df[processed_df['station_id'].notna() & (processed_df['station_id'] != '')]
    
    # Numeric columns with validation
    numeric_cols = ['lat', 'lon', 'bikes', 'spaces', 'total_docks', 'pedelecs', 'timezone_1', 'timezone_2']
    for col in numeric_cols:
        if col in processed_df.columns:
            processed_df[col] = pd.to_numeric(processed_df[col], errors='coerce')
            # Coordinate validation
            if col == 'lat':
                processed_df[col] = processed_df[col].clip(-90, 90)
            elif col == 'lon':
                processed_df[col] = processed_df[col].clip(-180, 180)
    
    # Boolean columns
    bool_cols = ['installed', 'locked', 'temporary']
    for col in bool_cols:
        if col in processed_df.columns:
            processed_df[col] = processed_df[col].astype('boolean')
    
    # String columns with length constraints
    if 'name' in processed_df.columns:
        processed_df['name'] = processed_df['name'].astype('string').str.slice(0, 200)
    if 's3_source_key' in processed_df.columns:
        processed_df['s3_source_key'] = processed_df['s3_source_key'].astype('string').str.slice(0, 500)
    
    # Partition-aware deduplication - keep most recent processed_at within partition
    original_count = len(processed_df)
    if 'processed_at' in processed_df.columns:
        processed_df = processed_df.sort_values('processed_at', ascending=False)
    
    processed_df = processed_df.drop_duplicates(
        subset=['station_id', 'timestamp'],
        keep='first'  # Keep most recent due to sorting
    ).reset_index(drop=True)
    
    # Final validation
    processed_df = processed_df.dropna(subset=['station_id', 'timestamp'])
    
    duplicates_removed = original_count - len(processed_df)
    context.log.info(f"Partition {partition_date} preprocessing: {original_count:,} → {len(processed_df):,} rows ({duplicates_removed:,} duplicates/invalid removed)")
    
    return processed_df

@asset(
    deps=[load_stations_to_postgres],
    partitions_def=daily_partitions,  # Make summary partitioned too
    group_name="database",
    compute_kind="postgresql"
)
def stations_data_summary(context: AssetExecutionContext):
    """
    Partition-aware summary statistics of the bike_stations table.
    """
    partition_date = context.partition_key
    
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        
        cursor = conn.cursor()
        
        # Get statistics for this partition
        cursor.execute("""
            SELECT 
                COUNT(*) as partition_records,
                COUNT(DISTINCT station_id) as unique_stations,
                MIN(timestamp) as earliest_record,
                MAX(timestamp) as latest_record,
                AVG(bikes) as avg_bikes,
                AVG(spaces) as avg_spaces
            FROM bike_stations
            WHERE DATE(timestamp) = %s
        """, (partition_date,))
        
        partition_stats = cursor.fetchone()
        
        # Get top stations for this partition
        cursor.execute("""
            SELECT station_id, name, COUNT(*) as record_count,
                   AVG(bikes) as avg_bikes, AVG(spaces) as avg_spaces
            FROM bike_stations
            WHERE DATE(timestamp) = %s
            GROUP BY station_id, name
            ORDER BY record_count DESC
            LIMIT 5
        """, (partition_date,))
        
        top_stations = cursor.fetchall()
        
        # Get overall database stats for context
        cursor.execute("""
            SELECT COUNT(*) as total_records,
                   COUNT(DISTINCT station_id) as total_unique_stations
            FROM bike_stations
        """)
        
        overall_stats = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        summary = {
            "partition_date": partition_date,
            "partition_records": partition_stats[0] if partition_stats else 0,
            "unique_stations_in_partition": partition_stats[1] if partition_stats else 0,
            "earliest_record": partition_stats[2] if partition_stats else None,
            "latest_record": partition_stats[3] if partition_stats else None,
            "avg_bikes": float(partition_stats[4]) if partition_stats and partition_stats[4] else 0,
            "avg_spaces": float(partition_stats[5]) if partition_stats and partition_stats[5] else 0,
            "top_stations_in_partition": [
                {
                    "station_id": s[0], 
                    "name": s[1], 
                    "record_count": s[2],
                    "avg_bikes": float(s[3]) if s[3] else 0,
                    "avg_spaces": float(s[4]) if s[4] else 0
                } 
                for s in top_stations
            ],
            "total_database_records": overall_stats[0] if overall_stats else 0,
            "total_unique_stations": overall_stats[1] if overall_stats else 0
        }
        
        context.log.info(f"Partition {partition_date} summary: {summary['partition_records']:,} records, {summary['unique_stations_in_partition']} unique stations")
        
        return summary
        
    except Exception as e:
        context.log.error(f"Failed to generate partition summary for {partition_date}: {str(e)}")
        raise