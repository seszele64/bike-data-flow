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
    Uses optimized staging table approach for efficient bulk insertion.
    """
    if wrm_stations_data.empty:
        context.log.info("No station data to load")
        return {"rows_inserted": 0, "rows_skipped": 0}
    
    try:
        # Create SQLAlchemy engine
        engine = create_engine(
            f'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}',
            pool_pre_ping=True,
            pool_recycle=300
        )
        
        # Preprocess data with validation
        processed_df = preprocess_dataframe(wrm_stations_data, context)
        
        if processed_df.empty:
            context.log.warning("No valid records after preprocessing")
            return {"rows_inserted": 0, "rows_skipped": len(wrm_stations_data)}
        
        # Get initial database state
        with engine.connect() as conn:
            total_existing = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
        
        context.log.info(f"Database state: {total_existing:,} existing records, processing {len(processed_df):,} new records")
        
        # Use optimized bulk insert with staging table
        rows_inserted = bulk_insert_with_staging(engine, processed_df, context)
        
        # Get final count
        with engine.connect() as conn:
            total_records = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
        
        rows_skipped = len(processed_df) - rows_inserted
        
        context.log.info(f"Load completed: {rows_inserted:,} inserted, {rows_skipped:,} skipped, {total_records:,} total records")
        
        engine.dispose()
        
        return {
            "rows_inserted": rows_inserted,
            "rows_skipped": rows_skipped,
            "total_records": total_records,
            "status": "completed"
        }
        
    except Exception as e:
        context.log.error(f"Failed to load stations to PostgreSQL: {str(e)}")
        raise

def bulk_insert_with_staging(engine, df, context):
    """
    Optimized bulk insert using temporary staging table with PostgreSQL COPY.
    """
    from sqlalchemy import text
    import uuid
    
    # Generate unique staging table name
    staging_table = f"temp_bike_stations_{uuid.uuid4().hex[:8]}"
    
    # Get count before insertion
    with engine.connect() as conn:
        before_count = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
    
    try:
        with engine.begin() as conn:
            # Create staging table with same structure as main table
            conn.execute(text(f"""
                CREATE TEMP TABLE {staging_table} 
                (LIKE bike_stations INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
            """))
            
            # Use pandas to_sql with optimized parameters
            df.to_sql(
                name=staging_table,
                con=conn,
                if_exists='append',
                index=False,
                chunksize=5000,
                method='multi'
            )
            
            # Insert from staging to main table with conflict resolution
            result = conn.execute(text(f"""
                INSERT INTO bike_stations
                SELECT * FROM {staging_table}
                ON CONFLICT (station_id, timestamp) DO NOTHING
            """))
            
            # Get final count
            after_count = conn.execute(text("SELECT COUNT(*) FROM bike_stations")).fetchone()[0]
            
            rows_inserted = after_count - before_count
            context.log.info(f"Staging table used: {staging_table}")
            context.log.info(f"Records processed: {len(df)}, inserted: {rows_inserted}")
            
            return rows_inserted
            
    except Exception as e:
        context.log.error(f"Bulk insert failed: {str(e)}")
        raise

def preprocess_dataframe(df, context):
    """
    Prepare DataFrame with proper data types and validation.
    """
    # Create a copy to avoid modifying original
    processed_df = df.copy()
    
    # Data type conversions with explicit handling
    processed_df['timestamp'] = pd.to_datetime(processed_df['timestamp'], utc=True).dt.tz_convert(None)
    processed_df['station_id'] = processed_df['station_id'].astype('string').str.slice(0, 100)
    
    # Handle numeric columns with proper null handling
    numeric_cols = ['lat', 'lon', 'bikes', 'spaces', 'total_docks', 'pedelecs', 'timezone_1', 'timezone_2']
    for col in numeric_cols:
        if col in processed_df.columns:
            processed_df[col] = pd.to_numeric(processed_df[col], errors='coerce')
    
    # Handle boolean columns
    bool_cols = ['installed', 'locked', 'temporary']
    for col in bool_cols:
        if col in processed_df.columns:
            processed_df[col] = processed_df[col].astype('boolean')
    
    # String length constraints
    if 'name' in processed_df.columns:
        processed_df['name'] = processed_df['name'].astype('string').str.slice(0, 200)
    if 's3_source_key' in processed_df.columns:
        processed_df['s3_source_key'] = processed_df['s3_source_key'].astype('string').str.slice(0, 500)
    
    # Deduplication with detailed logging
    original_count = len(processed_df)
    processed_df = processed_df.drop_duplicates(
        subset=['station_id', 'timestamp'],
        keep='last'
    ).reset_index(drop=True)
    
    duplicates_removed = original_count - len(processed_df)
    context.log.info(f"Data preprocessing: {original_count:,} → {len(processed_df):,} rows (removed {duplicates_removed:,} duplicates)")
    
    # Validation
    null_primary_keys = processed_df[['station_id', 'timestamp']].isnull().any(axis=1).sum()
    if null_primary_keys > 0:
        context.log.warning(f"Found {null_primary_keys} rows with null primary key values")
        processed_df = processed_df.dropna(subset=['station_id', 'timestamp'])
    
    return processed_df

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