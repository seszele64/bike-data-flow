from dagster import asset, AssetExecutionContext
import psycopg2
import pandas as pd
from sqlalchemy import create_engine, text
from ..config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD
)

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
    deps=[bike_stations_table, "wrm_stations_data"],
    group_name="database",
    compute_kind="postgresql",
    required_resource_keys={"s3_resource"}
)
def load_stations_to_postgres(context: AssetExecutionContext, wrm_stations_data: pd.DataFrame):
    """
    Loads new station data from deduplicated DataFrame to PostgreSQL.
    Uses pandas operations to efficiently identify and insert only new records.
    """
    if wrm_stations_data.empty:
        context.log.info("No station data to load")
        return {"rows_inserted": 0, "rows_skipped": 0}
    
    try:
        # Create SQLAlchemy engine for pandas compatibility
        engine = create_engine(
            f'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}'
        )
        
        # First, check how many records exist in total
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM bike_stations"))
            total_existing = result.fetchone()[0]
        
        context.log.info(f"Total records currently in database: {total_existing}")
        
        # Read existing records into DataFrame
        existing_df = pd.read_sql("""
            SELECT station_id, timestamp 
            FROM bike_stations
        """, engine)
        
        context.log.info(f"Found {len(existing_df)} existing records in database")
        context.log.info(f"Processing {len(wrm_stations_data)} records from source")
        
        # Prepare source data
        source_data = wrm_stations_data.copy()
        source_data['timestamp'] = pd.to_datetime(source_data['timestamp'])
        
        if not existing_df.empty:
            # Convert timestamp columns to ensure proper comparison
            existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'])
            
            # Use pandas merge to identify duplicates more reliably
            # Add a marker to identify existing records
            existing_df['exists'] = True
            
            # Merge to find which records already exist
            merged = source_data.merge(
                existing_df[['station_id', 'timestamp', 'exists']], 
                on=['station_id', 'timestamp'], 
                how='left'
            )
            
            # Filter out records that already exist
            new_records_df = merged[merged['exists'].isna()].copy()
            new_records_df = new_records_df.drop('exists', axis=1)  # Remove marker column
            
            skipped_count = len(source_data) - len(new_records_df)
        else:
            # No existing records, all are new
            new_records_df = source_data.copy()
            skipped_count = 0
        
        context.log.info(f"Found {len(new_records_df)} new records to insert")
        context.log.info(f"Skipping {skipped_count} existing records")
        
        # Insert new records using bulk insert with ON CONFLICT handling
        if not new_records_df.empty:
            # Ensure proper data types and handle NaN values
            new_records_df = new_records_df.copy()
            
            # Handle numeric columns
            numeric_cols = ['lat', 'lon', 'bikes', 'spaces', 'total_docks', 'pedelecs', 'timezone_1', 'timezone_2']
            for col in numeric_cols:
                if col in new_records_df.columns:
                    new_records_df[col] = pd.to_numeric(new_records_df[col], errors='coerce')
            
            # Handle boolean columns
            bool_cols = ['installed', 'locked', 'temporary']
            for col in bool_cols:
                if col in new_records_df.columns:
                    new_records_df[col] = new_records_df[col].astype('boolean')
            
            # Handle timestamp columns
            if 'processed_at' in new_records_df.columns:
                new_records_df['processed_at'] = pd.to_datetime(new_records_df['processed_at'], errors='coerce')
            
            # Debug: Show a sample of what we're about to insert
            context.log.info("Sample records to insert:")
            sample_records = new_records_df[['station_id', 'timestamp']].head(3)
            for _, row in sample_records.iterrows():
                context.log.info(f"  station_id: {row['station_id']}, timestamp: {row['timestamp']}")
            
            # Use custom insertion method with ON CONFLICT DO NOTHING
            rows_inserted = insert_with_conflict_handling(engine, new_records_df, context)
            
            context.log.info(f"Successfully inserted {rows_inserted} new records")
        else:
            rows_inserted = 0
        
        # Get final count using SQLAlchemy engine
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM bike_stations"))
            total_records = result.fetchone()[0]
        
        engine.dispose()
        
        return {
            "rows_inserted": rows_inserted,
            "rows_skipped": skipped_count,
            "total_records": total_records,
            "status": "completed"
        }
        
    except Exception as e:
        context.log.error(f"Failed to load stations to PostgreSQL: {str(e)}")
        raise

def insert_with_conflict_handling(engine, df, context):
    """
    Insert DataFrame to PostgreSQL with ON CONFLICT DO NOTHING to handle duplicates safely.
    """
    from sqlalchemy import text
    
    # Get the count before insertion
    with engine.connect() as conn:
        before_count = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
    
    # Prepare the INSERT statement with ON CONFLICT DO NOTHING
    columns = df.columns.tolist()
    placeholders = ', '.join([f':{col}' for col in columns])
    columns_str = ', '.join(columns)
    
    insert_sql = f"""
    INSERT INTO bike_stations ({columns_str})
    VALUES ({placeholders})
    ON CONFLICT (station_id, timestamp) DO NOTHING
    """
    
    # Convert DataFrame to list of dictionaries for bulk insert
    records = df.to_dict('records')
    
    # Execute bulk insert
    with engine.connect() as conn:
        conn.execute(text(insert_sql), records)
        conn.commit()
        
        # Get count after insertion
        after_count = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
    
    rows_inserted = after_count - before_count
    context.log.info(f"Database records before: {before_count}, after: {after_count}")
    
    return rows_inserted

@asset(
    deps=[load_stations_to_postgres],
    group_name="database",
    compute_kind="postgresql"
)
def stations_data_summary(context: AssetExecutionContext):
    """
    Provides summary statistics of the bike_stations table.
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
        
        # Get basic statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT station_id) as unique_stations,
                MIN(timestamp) as earliest_record,
                MAX(timestamp) as latest_record
            FROM bike_stations
        """)
        
        stats = cursor.fetchone()
        
        # Get top stations by record count
        cursor.execute("""
            SELECT station_id, name, COUNT(*) as record_count
            FROM bike_stations
            GROUP BY station_id, name
            ORDER BY record_count DESC
            LIMIT 5
        """)
        
        top_stations = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        summary = {
            "total_records": stats[0],
            "unique_stations": stats[1],
            "earliest_record": stats[2],
            "latest_record": stats[3],
            "top_stations": [
                {"station_id": s[0], "name": s[1], "record_count": s[2]} 
                for s in top_stations
            ]
        }
        
        context.log.info(f"Database summary: {summary}")
        
        return summary
        
    except Exception as e:
        context.log.error(f"Failed to generate stations summary: {str(e)}")
        raise