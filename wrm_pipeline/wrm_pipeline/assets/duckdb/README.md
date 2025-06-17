# DuckDB Analytics Views

This module creates DuckDB views for querying enhanced WRM (bike sharing) stations data stored in S3-compatible storage.

## Views Created

### 1. `wrm_stations_enhanced_data`
- **Description**: Main view containing all enhanced data (both stations and bikes)
- **Source**: Parquet files from S3 path `s3://bucket/bike-data/gen_info/enhanced/all/**/*.parquet`
- **Ordering**: Sorted by date DESC, file_timestamp DESC, station_id
- **Contains**: All records with enhanced metadata

### 2. `wrm_stations_only`
- **Description**: Filtered view showing only station records
- **Filter**: `record_type = 'station'`
- **Use case**: Analyzing station-level data (capacity, location, status)

### 3. `wrm_bikes_only`
- **Description**: Filtered view showing only bike records  
- **Filter**: `record_type = 'bike'`
- **Use case**: Analyzing individual bike data and movements

### 4. `wrm_stations_latest`
- **Description**: Latest snapshot of each station
- **Logic**: Uses ROW_NUMBER() to get the most recent record per station_id
- **Filter**: Only station records, ordered by date DESC, file_timestamp DESC
- **Use case**: Current state analysis, real-time dashboards

## Usage

### From DuckDB CLI
```bash
cd ~/data
duckdb analytics.duckdb
```

Configure S3 credentials first:
```sql
INSTALL httpfs;
LOAD httpfs;
SET s3_region='auto';
SET s3_access_key_id='your_access_key';
SET s3_secret_access_key='your_secret_key';
SET s3_endpoint='nbg1.your-objectstorage.com';
SET s3_use_ssl='true';
SET s3_url_style='path';
```

Query the views:
```sql
-- Get total record count
SELECT COUNT(*) FROM wrm_stations_enhanced_data;

-- View latest station status
SELECT station_id, name, bikes_available, docks_available 
FROM wrm_stations_latest 
LIMIT 10;

-- Analyze record type distribution
SELECT record_type, COUNT(*) 
FROM wrm_stations_enhanced_data 
GROUP BY record_type;
```

### From Python
```python
import duckdb
import os

db_path = os.path.join(os.path.expanduser("~"), "data", "analytics.duckdb")

with duckdb.connect(db_path) as conn:
    # Configure S3 credentials (same as CLI)
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    # ... set credentials ...
    
    # Query views
    result = conn.execute("SELECT * FROM wrm_stations_latest LIMIT 5;").fetchall()
    print(result)
```

## Asset Information

- **Asset Name**: `duckdb_enhanced_views`
- **Compute Kind**: `duckdb`
- **Group**: `analytics_views`
- **Dependencies**: Enhanced parquet files in S3 storage
- **Output**: String confirmation message

## Configuration Requirements

The asset requires the following environment variables:
- `HETZNER_ACCESS_KEY_ID`
- `HETZNER_SECRET_ACCESS_KEY` 
- `HETZNER_ENDPOINT_URL`
- `BUCKET_NAME`
- `WRM_STATIONS_S3_PREFIX`

## Database Location

Views are created in: `~/data/analytics.duckdb`