import duckdb
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Connect to DuckDB database in db folder
conn = duckdb.connect('db/bike_data.db')  # Load from db/bike_data.db

# Configure S3 credentials from environment variables
conn.execute(f"SET s3_endpoint='{os.getenv('HETZNER_ENDPOINT')}'")
conn.execute(f"SET s3_access_key_id='{os.getenv('HETZNER_ACCESS_KEY_ID')}'")
conn.execute(f"SET s3_secret_access_key='{os.getenv('HETZNER_SECRET_ACCESS_KEY')}'")
conn.execute("SET s3_use_ssl=true")
conn.execute("SET s3_url_style='path'")

conn.execute("ATTACH 'host=localhost port=5432 dbname=bike_db user=postgres' AS bike_db (TYPE postgres)")

# Get bucket and folder from environment
bucket_name = os.getenv('BUCKET_NAME')
target_folder = os.getenv('TARGET_S3_FOLDER')

# Create the processed_bike_stations table if it doesn't exist
conn.execute("""
    CREATE TABLE IF NOT EXISTS processed_bike_stations (
        id VARCHAR,
        name VARCHAR,
        lat DOUBLE,
        lon DOUBLE,
        timestamp TIMESTAMP,
        bikes BIGINT,
        spaces BIGINT,
        installed BOOLEAN,
        locked BOOLEAN,
        temporary BOOLEAN,
        total_docks BIGINT,
        givesbonus_acceptspedelecs_fbbattlevel VARCHAR,
        pedelecs BIGINT,
        dt VARCHAR,
        gmt_local_diff_sec VARCHAR,
        gmt_servertime_diff_sec VARCHAR,
        PRIMARY KEY (id, timestamp)
    );
""")

# Check current record count
current_count = conn.execute("SELECT COUNT(*) FROM processed_bike_stations").fetchone()[0]
print(f"Current records in database: {current_count}")

# Insert only new records from Parquet that don't exist in database
insert_result = conn.execute(f"""
    INSERT OR IGNORE INTO processed_bike_stations 
    SELECT 
        id, name, lat, lon, timestamp, bikes, spaces, installed, 
        locked, temporary, total_docks, givesbonus_acceptspedelecs_fbbattlevel, 
        pedelecs, dt, gmt_local_diff_sec, gmt_servertime_diff_sec
    FROM read_parquet('s3://{bucket_name}/{target_folder}gen_info/processed/dt=*/*.parquet') p
    WHERE NOT EXISTS (
        SELECT 1 FROM processed_bike_stations pbs 
        WHERE pbs.id = p.id AND pbs.timestamp = p.timestamp
    );
""")

# Check new record count
new_count = conn.execute("SELECT COUNT(*) FROM processed_bike_stations").fetchone()[0]
inserted_count = new_count - current_count

print(f"Records inserted: {inserted_count}")
print(f"Total records in database: {new_count}")

# Get summary of the data
summary = conn.execute("""
    SELECT 
        COUNT(*) as total_records,
        COUNT(DISTINCT id) as unique_stations,
        MIN(timestamp) as earliest_timestamp,
        MAX(timestamp) as latest_timestamp,
        COUNT(DISTINCT dt) as unique_dates
    FROM processed_bike_stations
""").df()

print("\nDatabase summary:")
print(summary)

# Optional: Show latest records per station
latest_records = conn.execute("""
    SELECT id, name, timestamp, bikes, spaces, dt
    FROM processed_bike_stations
    QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY timestamp DESC) = 1
    ORDER BY timestamp DESC
    LIMIT 10
""").df()

print("\nLatest records per station (top 10):")
print(latest_records)

# Select all records from processed_bike_stations
all_records = conn.execute("SELECT * FROM processed_bike_stations").df()
print("\nAll records from processed_bike_stations:")
print(all_records)

# Print column information for processed_bike_stations table
columns_info = conn.execute("PRAGMA table_info(processed_bike_stations)").df()
print("\nColumns in processed_bike_stations table:")
print(columns_info)

conn.close()