import duckdb
import os
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Connect to DuckDB
conn = duckdb.connect()

# Configure S3 credentials from environment variables
conn.execute(f"SET s3_endpoint='{os.getenv('HETZNER_ENDPOINT')}'")
conn.execute(f"SET s3_access_key_id='{os.getenv('HETZNER_ACCESS_KEY_ID')}'")
conn.execute(f"SET s3_secret_access_key='{os.getenv('HETZNER_SECRET_ACCESS_KEY')}'")
conn.execute("SET s3_use_ssl=true")
conn.execute("SET s3_url_style='path'")

# Get bucket and folder from environment
bucket_name = os.getenv('BUCKET_NAME')
target_folder = os.getenv('TARGET_S3_FOLDER')

# Perform deduplication query on bike station data
deduplicated_rel = conn.sql(f"""
    SELECT 
        id,
        name,
        lat,
        lon,
        MAX(timestamp) as latest_timestamp,
        LAST(bikes ORDER BY timestamp) as latest_bikes,
        LAST(spaces ORDER BY timestamp) as latest_spaces,
        LAST(installed ORDER BY timestamp) as latest_installed,
        LAST(locked ORDER BY timestamp) as latest_locked,
        LAST(temporary ORDER BY timestamp) as latest_temporary,
        LAST(total_docks ORDER BY timestamp) as latest_total_docks,
        LAST(givesbonus_acceptspedelecs_fbbattlevel ORDER BY timestamp) as latest_status,
        LAST(pedelecs ORDER BY timestamp) as latest_pedelecs,
        dt
    FROM read_parquet('s3://{bucket_name}/{target_folder}gen_info/processed/dt=*/*.parquet')
    GROUP BY id, name, lat, lon, dt
    ORDER BY latest_timestamp DESC
""")

# Preview the results
print("Deduplicated bike station data preview:")
print(deduplicated_rel.limit(10).df())

# Write deduplicated data back to S3
output_path = f's3://{bucket_name}/{target_folder}gen_info/deduplicated/deduplicated_bike_stations.parquet'
deduplicated_rel.write_parquet(output_path)

print(f"Deduplicated data written to: {output_path}")

# Optional: Get summary statistics
summary = conn.sql(f"""
    SELECT 
        COUNT(*) as total_records,
        COUNT(DISTINCT id) as unique_stations,
        COUNT(DISTINCT name) as unique_station_names,
        MIN(latest_timestamp) as earliest_timestamp,
        MAX(latest_timestamp) as latest_timestamp,
        AVG(latest_bikes) as avg_bikes_available,
        AVG(latest_spaces) as avg_spaces_available
    FROM (
        SELECT 
            id,
            name,
            lat,
            lon,
            MAX(timestamp) as latest_timestamp,
            LAST(bikes ORDER BY timestamp) as latest_bikes,
            LAST(spaces ORDER BY timestamp) as latest_spaces,
            LAST(installed ORDER BY timestamp) as latest_installed,
            LAST(locked ORDER BY timestamp) as latest_locked,
            LAST(temporary ORDER BY timestamp) as latest_temporary,
            LAST(total_docks ORDER BY timestamp) as latest_total_docks,
            LAST(givesbonus_acceptspedelecs_fbbattlevel ORDER BY timestamp) as latest_status,
            LAST(pedelecs ORDER BY timestamp) as latest_pedelecs,
            dt
        FROM read_parquet('s3://{bucket_name}/{target_folder}gen_info/processed/dt=*/*.parquet')
        GROUP BY id, name, lat, lon, dt
    )
""").df()

print("\nDeduplication summary:")
print(summary)

# Save full summary to text file
summary_text = f"""
Bike Station Deduplication Summary
==================================
Generated on: {pd.Timestamp.now()}

Summary Statistics:
{summary.to_string(index=False)}

Details:
- Total Records: {summary['total_records'].iloc[0]}
- Unique Stations: {summary['unique_stations'].iloc[0]}
- Unique Station Names: {summary['unique_station_names'].iloc[0]}
- Earliest Timestamp: {summary['earliest_timestamp'].iloc[0]}
- Latest Timestamp: {summary['latest_timestamp'].iloc[0]}
- Average Bikes Available: {summary['avg_bikes_available'].iloc[0]:.2f}
- Average Spaces Available: {summary['avg_spaces_available'].iloc[0]:.2f}

Data Source: s3://{bucket_name}/{target_folder}gen_info/processed/dt=*/*.parquet
Output Location: {output_path}
"""

# Write to local file
with open('deduplication_summary.txt', 'w') as f:
    f.write(summary_text)

print("Full summary saved to: deduplication_summary.txt")

conn.close()