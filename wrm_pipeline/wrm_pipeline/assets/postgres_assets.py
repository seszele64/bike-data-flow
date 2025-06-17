from dagster import asset, AssetExecutionContext, DailyPartitionsDefinition
import psycopg2
import pandas as pd
from sqlalchemy import create_engine, text
import uuid
from datetime import datetime
from io import BytesIO
from ..config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    BUCKET_NAME
)

# Define partitions to match your deduplicated asset
daily_partitions = DailyPartitionsDefinition(start_date="2025-05-01")

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
    deps=[bike_stations_table, "wrm_stations_deduplicated"],  # Changed dependency
    partitions_def=daily_partitions,
    group_name="database",
    compute_kind="postgresql",
    required_resource_keys={"s3_resource"}
)
def load_stations_to_postgres(context: AssetExecutionContext, wrm_stations_deduplicated: str):
    """
    Load pre-deduplicated station data directly to PostgreSQL.
    Data is already cleaned and optimized for database loading.
    """
    partition_date = context.partition_key
    context.log.info(f"Loading deduplicated data for partition: {partition_date}")
    
    if not wrm_stations_deduplicated:
        context.log.info(f"No deduplicated data for partition {partition_date}")
        return {"partition": partition_date, "rows_loaded": 0, "status": "no_data"}
    
    try:
        # Load pre-deduplicated data from S3
        s3_client = context.resources.s3_resource
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_stations_deduplicated)
        df = pd.read_parquet(BytesIO(response['Body'].read()))
        
        if df.empty:
            context.log.info(f"Empty deduplicated dataset for partition {partition_date}")
            return {"partition": partition_date, "rows_loaded": 0, "status": "empty"}
        
        # Create SQLAlchemy engine
        engine = create_engine(
            f'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}',
            pool_pre_ping=True,
            pool_recycle=300
        )
        
        # Since data is already deduplicated, we can use simpler upsert
        result = simple_upsert_deduplicated_data(engine, df, partition_date, context)
        
        engine.dispose()
        
        return {
            "partition": partition_date,
            "rows_loaded": result["rows_affected"],
            "total_processed": len(df),
            "status": "completed"
        }
        
    except Exception as e:
        context.log.error(f"Failed to load deduplicated data for partition {partition_date}: {str(e)}")
        raise

def simple_upsert_deduplicated_data(engine, df, partition_date, context):
    """
    Simplified upsert for pre-deduplicated data.
    Since deduplication is handled upstream, we can use more efficient loading.
    """
    with engine.begin() as conn:
        # Create a temporary table for this batch
        temp_table = f"temp_dedup_{partition_date.replace('-', '_')}_{uuid.uuid4().hex[:8]}"
        
        # Load data directly since it's already clean
        df.to_sql(
            name=temp_table,
            con=conn,
            if_exists='replace',
            index=False,
            method='multi'
        )
        
        # Simple upsert since data is already deduplicated
        result = conn.execute(text(f"""
            INSERT INTO bike_stations
            SELECT * FROM {temp_table}
            ON CONFLICT (station_id, timestamp) 
            DO UPDATE SET
                name = EXCLUDED.name,
                lat = EXCLUDED.lat,
                lon = EXCLUDED.lon,
                bikes = EXCLUDED.bikes,
                spaces = EXCLUDED.spaces,
                installed = EXCLUDED.installed,
                locked = EXCLUDED.locked,
                temporary = EXCLUDED.temporary,
                total_docks = EXCLUDED.total_docks,
                givesbonus_acceptspedelecs_fbbattlevel = EXCLUDED.givesbonus_acceptspedelecs_fbbattlevel,
                pedelecs = EXCLUDED.pedelecs,
                timezone_1 = EXCLUDED.timezone_1,
                timezone_2 = EXCLUDED.timezone_2,
                date = EXCLUDED.date,
                processed_at = EXCLUDED.processed_at,
                s3_source_key = EXCLUDED.s3_source_key
        """))
        
        rows_affected = result.rowcount
        context.log.info(f"Loaded {rows_affected} records for partition {partition_date}")
        
        return {"rows_affected": rows_affected}

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