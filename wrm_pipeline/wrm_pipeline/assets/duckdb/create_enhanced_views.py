from dagster import asset, AssetExecutionContext
import duckdb
import os
from ...config import HETZNER_ACCESS_KEY_ID, HETZNER_SECRET_ACCESS_KEY, HETZNER_ENDPOINT_URL, BUCKET_NAME, WRM_STATIONS_S3_PREFIX

@asset(
    name="duckdb_enhanced_views",
    compute_kind="duckdb",
    group_name="analytics_views"
)
def create_duckdb_enhanced_views(context: AssetExecutionContext) -> str:
    """Create DuckDB views for enhanced WRM stations data"""
    
    db_path = os.path.join(os.path.expanduser("~"), "data", "analytics.duckdb")
    
    with duckdb.connect(db_path) as conn:
        # Install and load required extensions
        conn.execute("INSTALL httpfs;")
        conn.execute("LOAD httpfs;")
        
        # Configure S3 credentials - Fix the endpoint URL
        conn.execute("SET s3_region='auto';")
        conn.execute(f"SET s3_access_key_id='{HETZNER_ACCESS_KEY_ID}';")
        conn.execute(f"SET s3_secret_access_key='{HETZNER_SECRET_ACCESS_KEY}';")
        
        # Fix the endpoint URL - DuckDB expects hostname only, not full URL
        if HETZNER_ENDPOINT_URL.startswith(('http://', 'https://')):
            # Remove protocol if present
            endpoint_hostname = HETZNER_ENDPOINT_URL.replace('https://', '').replace('http://', '')
        else:
            endpoint_hostname = HETZNER_ENDPOINT_URL
            
        conn.execute(f"SET s3_endpoint='{endpoint_hostname}';")
        conn.execute("SET s3_use_ssl='true';")
        conn.execute("SET s3_url_style='path';")
        
        # Debug: Log the configuration
        context.log.info(f"S3 Endpoint (hostname only): {endpoint_hostname}")
        context.log.info(f"Bucket: {BUCKET_NAME}")
        context.log.info(f"Prefix: {WRM_STATIONS_S3_PREFIX}")
        
        # Create the views directly here instead of importing
        s3_path = f"s3://{BUCKET_NAME}/{WRM_STATIONS_S3_PREFIX}enhanced/all/**/*.parquet"
        context.log.info(f"S3 Path: {s3_path}")
        
        # Test S3 connection first
        try:
            test_result = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{s3_path}') LIMIT 1;").fetchone()
            context.log.info(f"S3 connection test successful")
        except Exception as e:
            context.log.error(f"S3 connection test failed: {e}")
            # Try to list available files for debugging
            try:
                list_path = f"s3://{BUCKET_NAME}/{WRM_STATIONS_S3_PREFIX}enhanced/all/"
                files = conn.execute(f"SELECT * FROM glob('{list_path}*') LIMIT 10;").fetchall()
                context.log.info(f"Available files in {list_path}: {files}")
            except Exception as list_error:
                context.log.error(f"Could not list files: {list_error}")
            raise
        
        # Main enhanced data view
        view_sql = f"""
        CREATE OR REPLACE VIEW wrm_stations_enhanced_data AS
        SELECT *
        FROM read_parquet('{s3_path}')
        ORDER BY date DESC, file_timestamp DESC, station_id;
        """
        conn.execute(view_sql)
        context.log.info("Created wrm_stations_enhanced_data view")
        
        # Stations only view
        conn.execute("""
        CREATE OR REPLACE VIEW wrm_stations_only AS
        SELECT * FROM wrm_stations_enhanced_data WHERE record_type = 'station';
        """)
        context.log.info("Created wrm_stations_only view")
        
        # Bikes only view
        conn.execute("""
        CREATE OR REPLACE VIEW wrm_bikes_only AS  
        SELECT * FROM wrm_stations_enhanced_data WHERE record_type = 'bike';
        """)
        context.log.info("Created wrm_bikes_only view")
        
        # Latest stations view
        conn.execute("""
        CREATE OR REPLACE VIEW wrm_stations_latest AS
        SELECT *
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY station_id ORDER BY date DESC, file_timestamp DESC) as rn
            FROM wrm_stations_enhanced_data
            WHERE record_type = 'station'
        ) t
        WHERE rn = 1;
        """)
        context.log.info("Created wrm_stations_latest view")
        
        # Test the views
        try:
            count_result = conn.execute("SELECT COUNT(*) FROM wrm_stations_enhanced_data;").fetchone()
            context.log.info(f"Total records in enhanced view: {count_result[0]}")
            
            # Record type distribution
            type_dist = conn.execute("""
                SELECT record_type, COUNT(*) as count 
                FROM wrm_stations_enhanced_data 
                GROUP BY record_type;
            """).fetchall()
            context.log.info(f"Record type distribution: {dict(type_dist)}")
            
            # Show available views
            views = conn.execute("SHOW TABLES;").fetchall()
            context.log.info(f"Available tables/views: {[v[0] for v in views]}")
            
        except Exception as e:
            context.log.error(f"Error testing views: {e}")
            raise
        
        return "DuckDB enhanced views created successfully"