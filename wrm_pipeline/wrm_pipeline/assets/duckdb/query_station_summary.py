from dagster import asset, AssetExecutionContext
import duckdb
import os

from ...config import HETZNER_ACCESS_KEY_ID, HETZNER_SECRET_ACCESS_KEY, HETZNER_ENDPOINT
from .create_enhanced_views import create_duckdb_enhanced_views


@asset(
    name="station_summary",
    compute_kind="duckdb",
    group_name="analytics_views"
)
def query_station_summary(context: AssetExecutionContext, duckdb_enhanced_views: str) -> dict:
    """Query station summary from DuckDB views"""
    
    db_path = os.path.join(os.path.expanduser("~"), "data", "analytics.duckdb")
    
    with duckdb.connect(db_path) as conn:
        # Configure S3 credentials for querying views
        conn.execute("INSTALL httpfs;")
        conn.execute("LOAD httpfs;")
        conn.execute("SET s3_region='auto';")
        conn.execute(f"SET s3_access_key_id='{HETZNER_ACCESS_KEY_ID}';")
        conn.execute(f"SET s3_secret_access_key='{HETZNER_SECRET_ACCESS_KEY}';")
        
            
        conn.execute(f"SET s3_endpoint='{HETZNER_ENDPOINT}';")
        conn.execute("SET s3_use_ssl='true';")
        conn.execute("SET s3_url_style='path';")
        
        # Get summary stats
        total_records = conn.execute("SELECT COUNT(*) FROM wrm_stations_enhanced_data;").fetchone()[0]
        
        latest_stations = conn.execute("""
            SELECT station_id, name, bikes, timestamp as last_reported
            FROM wrm_stations_latest 
            ORDER BY timestamp DESC 
            LIMIT 10;
        """).fetchall()
        
        context.log.info(f"Total records: {total_records}")
        context.log.info(f"Latest stations sample: {latest_stations[:3]}")
        
        return {
            "total_records": total_records,
            "latest_stations_sample": latest_stations
        }