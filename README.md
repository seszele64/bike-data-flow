# Bike Data Flow Pipeline

A comprehensive data orchestration pipeline built with Dagster for processing and analyzing bike-sharing station data from Wrocław's WRM (Wrocławski Rower Miejski) system.

## 🎯 Goals

- **Real-time Data Ingestion**: Automatically fetch bike station data from WRM API
- **Data Processing & Transformation**: Clean, process, and deduplicate raw station data
- **Data Storage & Analytics**: Store processed data in PostgreSQL with efficient partitioning
- **Data Quality & Monitoring**: Provide comprehensive summaries and processing statistics
- **Scalable Architecture**: Handle large volumes of historical and real-time bike station data

## 🛠️ Tech Stack

### Core Technologies
- **Dagster**: Data orchestration and pipeline management
- **PostgreSQL**: Primary data warehouse for processed station data
- **AWS S3**: Object storage for raw and processed data files
- **DuckDB**: High-performance analytical processing for deduplication
- **Pandas**: Data manipulation and transformation

### Supporting Libraries
- **SQLAlchemy**: Database ORM and connection management
- **psycopg2**: PostgreSQL database adapter
- **ftfy**: Text encoding fixes
- **requests**: HTTP API calls

## 📊 Data Flow Architecture

### Pipeline Overview
```
WRM API → Raw Data (S3) → Processing → Deduplication → PostgreSQL → Analytics
```

### Asset Groups

#### 1. **Raw Data Ingestion** (`wrm_stations_raw`)
- [`wrm_stations_raw_data_asset`](wrm_pipeline/wrm_pipeline/assets/stations.py): Downloads fresh data from WRM API
- [`s3_raw_stations_list`](wrm_pipeline/wrm_pipeline/assets/raw_stations.py): Lists all raw files in S3
- [`wrm_raw_stations_data`](wrm_pipeline/wrm_pipeline/assets/raw_stations.py): Combines multiple raw files into DataFrame

#### 2. **Data Processing** (`wrm_stations1`, `wrm_stations_raw`)
- [`wrm_stations_processed_asset`](wrm_pipeline/wrm_pipeline/assets/stations.py): Processes raw data and converts to Parquet
- [`wrm_stations_batch_processor`](wrm_pipeline/wrm_pipeline/assets/raw_stations.py): Batch processes multiple raw files
- [`wrm_stations_processing_summary`](wrm_pipeline/wrm_pipeline/assets/raw_stations.py): Provides processing pipeline statistics

#### 3. **Deduplication** (`wrm_stations`)
- [`s3_processed_stations_list`](wrm_pipeline/wrm_pipeline/assets/stations_deduplicated.py): Lists processed files for deduplication
- [`wrm_stations_daily_deduplicated`](wrm_pipeline/wrm_pipeline/assets/stations_deduplicated.py): Daily partitioned deduplication using DuckDB

#### 4. **Database Operations** (`database`)
- [`postgres_connection`](wrm_pipeline/wrm_pipeline/assets/postgres_assets.py): Database connection management
- [`bike_stations_table`](wrm_pipeline/wrm_pipeline/assets/postgres_assets.py): Creates/manages bike stations table
- [`bike_failures_table`](wrm_pipeline/wrm_pipeline/assets/postgres_assets.py): Creates failure tracking table
- [`load_stations_to_postgres`](wrm_pipeline/wrm_pipeline/assets/postgres_assets.py): Loads deduplicated data to PostgreSQL
- [`stations_data_summary`](wrm_pipeline/wrm_pipeline/assets/postgres_assets.py): Generates partition-aware analytics

## 🔄 Data Processing Flow

### 1. **Data Ingestion**
```python
WRM API → S3 Raw Storage (Partitioned by date)
- Format: Text files with CSV data
- Partition: dt=YYYY-MM-DD/
- Encoding: UTF-8 with automatic fixing
```

### 2. **Data Processing**
```python
Raw Data → Processed Data (Parquet)
- Column splitting and type conversion
- Timestamp normalization
- Data validation and cleaning
- Parquet optimization for analytics
```

### 3. **Deduplication**
```python
Processed Files → Daily Deduplicated Data
- DuckDB-powered efficient deduplication
- Partition-aware processing
- Memory-optimized operations
```

### 4. **Database Loading**
```python
Deduplicated Data → PostgreSQL
- Partition-aware upsert operations
- Conflict resolution (station_id + timestamp)
- Performance optimized bulk loading
```

### 5. **Analytics & Monitoring**
```python
PostgreSQL → Summary Statistics
- Daily partition summaries
- Top stations analysis
- Data quality metrics
- Processing success rates
```

## 🔍 Automated Monitoring & Sensors

The pipeline includes intelligent sensors that monitor S3 for new data and automatically trigger processing:

### Sensor Architecture
```
S3 Raw Files → Raw Sensor → Processing → S3 Processed Files → Processed Sensor → Database Loading
```

### Active Sensors

#### 1. **Raw Data Sensor** (`s3_raw_stations_sensor`)
- **File**: [`stations_sensor.py`](wrm_pipeline/wrm_pipeline/sensors/stations_sensor.py)
- **Monitoring**: S3 bucket for new raw `.txt` files
- **Frequency**: Every 60 seconds
- **Triggers**: [`wrm_stations_processed`] asset materialization
- **Features**:
  - Cursor-based tracking to avoid reprocessing
  - Unique run identification using S3 keys
  - Comprehensive error handling and logging
  - Batch processing of multiple new files

#### 2. **Processed Data Sensor** (`s3_processed_stations_sensor`)
- **File**: [`s3_processed_to_postgres_sensor.py`](wrm_pipeline/wrm_pipeline/sensors/s3_processed_to_postgres_sensor.py)
- **Monitoring**: S3 bucket for new processed `.parquet` files
- **Frequency**: Every 120 seconds (2 minutes)
- **Triggers**: [`wrm_stations_etl_job`] containing `bike_stations_table` and dependencies
- **Features**:
  - Automatic database loading when new processed files arrive
  - Latest file tracking with cursor management
  - Rich metadata tagging for run tracking
  - Error resilience with detailed logging

### Sensor Features

#### **Intelligent File Detection**
```python
# Raw sensor monitors for new .txt files
if key.endswith('.txt'):
    if last_processed_key is None or key > last_processed_key:
        txt_files.append(key)

# Processed sensor monitors for new .parquet files  
if key.endswith('.parquet'):
    if last_processed_key is None or key > last_processed_key:
        new_files.append(key)
```

#### **Cursor-Based State Management**
- Sensors maintain state using Dagster cursors
- Prevents reprocessing of already handled files
- Enables incremental processing from last checkpoint
- Automatic recovery from sensor restarts

#### **Error Handling & Resilience**
```python
try:
    # S3 monitoring logic
    response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=s3_prefix)
    # Processing logic...
except Exception as e:
    context.log.error(f"Error checking S3: {e}")
    return SkipReason(f"Error checking S3: {str(e)}")
```

#### **Rich Metadata & Tagging**
- Each sensor run includes comprehensive tags
- S3 bucket and key information
- File count and processing metadata
- Unique run identification for tracking

### Sensor Coordination

The sensors work together to create a fully automated pipeline:

1. **Raw Sensor**: Detects new data files → Triggers processing
2. **Processed Sensor**: Detects processed files → Triggers database loading
3. **Automatic Backfill**: Handles historical data gaps automatically
4. **State Recovery**: Resumes from last successful checkpoint

## 📁 Project Structure

```
wrm_pipeline/
├── assets/
│   ├── __init__.py              # Asset imports and exports
│   ├── assets.py               # Central asset aggregator
│   ├── stations.py             # Raw data ingestion from API
│   ├── raw_stations.py         # Raw data processing and batch operations
│   ├── stations_deduplicated.py # DuckDB-powered deduplication
│   └── postgres_assets.py      # Database operations and analytics
├── sensors/
│   ├── __init__.py             # Sensor imports and exports
│   ├── stations_sensor.py      # Raw data monitoring sensor
│   └── s3_processed_to_postgres_sensor.py # Processed data monitoring sensor
└── config.py                   # Configuration management
```

## 🚀 Key Features

### **Partitioned Processing**
- Daily partitions for efficient data management
- Partition-aware upsert operations
- Scalable historical data processing

### **Data Quality Assurance**
- Encoding issue detection and fixing
- Duplicate record identification and removal
- Data validation and type checking
- Processing success monitoring

### **Performance Optimization**
- DuckDB for high-performance analytics
- Parquet format for efficient storage
- Batch processing capabilities
- Connection pooling for database operations

### **Automated Monitoring & Orchestration**
- Real-time S3 file monitoring
- Automatic pipeline triggering
- State management and recovery
- Comprehensive error handling

### **Monitoring & Observability**
- Comprehensive logging throughout pipeline
- Processing statistics and metadata
- Success/failure tracking
- Data lineage visualization in Dagster UI

## 📈 Data Schema

### Bike Stations Table
```sql
bike_stations (
    id UUID PRIMARY KEY,
    station_id INTEGER,
    timestamp TIMESTAMP,
    name TEXT,
    bikes INTEGER,
    spaces INTEGER,
    date DATE,
    processed_at TIMESTAMP,
    timezone_1 INTEGER,
    timezone_2 INTEGER
)
```

### Partitioning Strategy
- **S3**: Date-based partitioning (`dt=YYYY-MM-DD`)
- **PostgreSQL**: Efficient indexing on `station_id` and `timestamp`
- **Dagster**: Daily partitions starting from 2025-05-10

## 🔧 Configuration

Key configuration parameters are managed in [`config.py`](wrm_pipeline/wrm_pipeline/config.py):
- Database connection settings
- S3 bucket and prefix configurations
- API endpoints
- Processing parameters
- Sensor intervals and monitoring settings

## 📊 Monitoring & Analytics

The pipeline provides comprehensive monitoring through:
- **Real-time Processing Stats**: Track success/failure rates
- **Data Quality Metrics**: Monitor duplicates, missing data, encoding issues
- **Performance Metrics**: Processing times, record counts, storage usage
- **Business Analytics**: Station utilization, bike availability trends
- **Automated Alerts**: Sensor-based notifications for pipeline issues

## 🚦 Getting Started

1. **Configure Resources**: Set up S3, PostgreSQL connections
2. **Deploy Sensors**: Enable automatic monitoring sensors
3. **Run Initial Backfill**: Process historical data using partitions
4. **Monitor Pipeline**: Use Dagster UI for real-time observability
5. **Scale Operations**: Sensors automatically handle new data as it arrives

This pipeline efficiently handles the complete lifecycle of bike-sharing data from ingestion to analytics, with intelligent automation that ensures data flows seamlessly from source to destination without manual intervention.
