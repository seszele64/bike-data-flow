## `stations.py`

### Overview
The pipeline has 4 main assets that process bike station data through a series of stages:

### 1. **Raw Data Acquisition** (`wrm_stations_raw_data_asset`)
- **Purpose**: Downloads current bike station data from WRM API
- **Key Features**:
  - Fetches data from `https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw`
  - Fixes text encoding issues using `ftfy`
  - **Duplicate Detection**: Compares hash of new data with most recent file to avoid storing identical data
  - Stores raw data in S3 with timestamp-based naming: `raw/dt=YYYY-MM-DD/wrm_stations_YYYY-MM-DD_HH-MM-SS.txt`
- **Not Partitioned**: Runs on-demand to fetch current data

### 2. **Data Processing** (`wrm_stations_all_processed_asset`)
- **Purpose**: Validates and processes raw data into structured format
- **Partitioned**: Daily partitions for historical data processing
- **Key Processing Steps**:
  - Reads all raw files for a given date partition
  - Parses CSV data and splits timestamp field (`timestamp|gmt_local_diff_sec|gmt_servertime_diff_sec`)
  - **Record Classification**: Categorizes records as:
    - `station`: Numeric IDs, non-BIKE names
    - `bike`: IDs starting with 'fb', names starting with 'BIKE'
    - `unknown`: Everything else
  - **Data Validation**: Uses Pandera schema to validate data types and constraints
  - Converts boolean fields from strings (`'true'/'false'` → `True/False`)
  - Stores processed data as Parquet: `processed/all/dt=YYYY-MM-DD/all_processed_TIMESTAMP.parquet`

### 3. **Station Data Extraction** (`wrm_stations_data_asset`)
- **Purpose**: Filters processed data to extract only station records
- **Depends on**: `wrm_stations_all_processed_asset`
- Removes `record_type` column after filtering
- Stores in: `processed/stations/dt=YYYY-MM-DD/stations_TIMESTAMP.parquet`

### 4. **Bike Data Extraction** (`wrm_bikes_data_asset`)
- **Purpose**: Filters processed data to extract only bike records
- **Depends on**: `wrm_stations_all_processed_asset`
- Same structure as station extraction but for bike records
- Stores in: `processed/bikes/dt=YYYY-MM-DD/bikes_TIMESTAMP.parquet`

### Data Flow Architecture
```
API → Raw Data (S3) → All Processed (S3) → Stations Data (S3)
                                        → Bikes Data (S3)
```

### Key Features
- **Error Handling**: Comprehensive try-catch blocks with detailed logging
- **Metadata Tracking**: Rich metadata for monitoring data quality and processing stats
- **Flexible Processing**: Handles multiple raw files per day
- **Data Quality**: Schema validation ensures data integrity
- **Duplicate Prevention**: Hash-based duplicate detection for raw data
- **S3 Storage**: All data stored in S3 with organized partitioning structure

This pipeline enables reliable ingestion and processing of real-time bike-sharing data while maintaining data quality and providing clear separation between stations and individual bikes.

## `duckdb_assets.py`