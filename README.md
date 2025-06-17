# Bike Data Flow Pipeline

A comprehensive data orchestration pipeline built with Dagster for processing and analyzing bike-sharing station data from Wrocław's WRM (Wrocławski Rower Miejski) system.

## 🎯 Goals

- **Real-time Data Ingestion**: Automatically fetch bike station data from WRM API
- **Data Processing & Transformation**: Clean, process, and enhance raw station data
- **Data Storage & Analytics**: Store processed data in S3 and provide DuckDB analytics views
- **Data Quality & Monitoring**: Provide comprehensive summaries and processing statistics
- **Scalable Architecture**: Handle large volumes of historical and real-time bike station data

## 🛠️ Tech Stack

### Core Technologies
- **Dagster**: Data orchestration and pipeline management
- **S3-Compatible Storage**: Object storage for raw, processed, and enhanced data files
- **DuckDB**: High-performance analytical processing and querying
- **Apache Iceberg**: Modern table format for enhanced data management
- **Pandas**: Data manipulation and transformation

### Supporting Libraries
- **requests**: HTTP API calls for data ingestion
- **ftfy**: Text encoding fixes
- **pandera**: Data validation and schema enforcement
- **pydantic**: Data modeling and validation

## 📊 Data Flow Architecture

### Pipeline Overview
```
WRM API → Raw Data (S3) → Processing → Enhancement → Analytics (DuckDB)
```

### Asset Groups

#### 1. **Data Acquisition** (`wrm_data_acquisition`)
- [`wrm_stations_raw_data`](wrm_pipeline/wrm_pipeline/assets/stations/raw_all.py): Downloads fresh data from WRM API with deduplication

#### 2. **Data Processing** (`wrm_data_processing`) 
- [`wrm_stations_processed_data_all`](wrm_pipeline/wrm_pipeline/assets/stations/processed_all.py): Processes raw data and converts to Parquet with validation

#### 3. **Enhanced Data** (`enhanced_data`)
- [`wrm_stations_enhanced_data_all`](wrm_pipeline/wrm_pipeline/assets/stations/enhanced_all.py): Creates enhanced datasets with metadata enrichment
- [`daily_station_data`](wrm_pipeline/wrm_pipeline/assets/stations/enhanced_station_data.py): Daily partitioned station-only data with Iceberg support
- [`daily_bike_data`](wrm_pipeline/wrm_pipeline/assets/stations/enhanced_bike_data.py): Daily partitioned bike-only data

#### 4. **Analytics Views** (`analytics_views`)
- [`duckdb_enhanced_views`](wrm_pipeline/wrm_pipeline/assets/duckdb/create_enhanced_views.py): Creates DuckDB views for enhanced data analysis
- [`station_summary`](wrm_pipeline/wrm_pipeline/assets/duckdb/query_station_summary.py): Generates station summary statistics
- [`bike_density_spatial_analysis`](wrm_pipeline/wrm_pipeline/assets/duckdb/bike_spatial_density_analysis.py): Spatial density analysis for bike distribution
- [`bike_density_map`](wrm_pipeline/wrm_pipeline/assets/duckdb/bike_spatial_density_analysis.py): Interactive density mapping

## 🔄 Data Processing Flow

### 1. **Data Ingestion**
```python
WRM API → S3 Raw Storage (Partitioned by date)
- Format: Text files with CSV data
- Partition: dt=YYYY-MM-DD/
- Encoding: UTF-8 with automatic fixing
- Deduplication: Hash-based duplicate detection
```

### 2. **Data Processing**
```python
Raw Data → Processed Data (Parquet)
- Column splitting and type conversion
- Timestamp normalization
- Data validation with Pandera schemas
- Parquet optimization for analytics
```

### 3. **Data Enhancement**
```python
Processed Files → Enhanced Data
- Metadata enrichment (file timestamps, processing dates)
- Record type classification (station vs bike data)
- Daily partitioning for efficient access
- Apache Iceberg table format support
```

### 4. **Analytics & Views**
```python
Enhanced Data → DuckDB Views
- High-performance analytical views
- Station-only and bike-only filtered views
- Latest station status views
- Spatial analysis capabilities
```

## 🗄️ Data Storage Architecture

### S3 Storage Structure
```
s3://bucket/bike-data/gen_info/
├── raw/dt=YYYY-MM-DD/           # Raw API data files
├── processed/all/dt=YYYY-MM-DD/ # Processed Parquet files
└── enhanced/all/dt=YYYY-MM-DD/  # Enhanced datasets with metadata
```

### DuckDB Analytics Database
- **Location**: `~/data/analytics.duckdb`
- **Views**: Enhanced data views for querying and analysis
- **S3 Integration**: Direct querying of S3-stored Parquet files
- **Performance**: Optimized for analytical workloads

## 📊 DuckDB Analytics Views

### Available Views

#### 1. **wrm_stations_enhanced_data**
- **Description**: Main view containing all enhanced data (stations and bikes)
- **Source**: S3 Parquet files from enhanced dataset
- **Ordering**: Sorted by date DESC, file_timestamp DESC, station_id

#### 2. **wrm_stations_only**
- **Description**: Filtered view showing only station records
- **Filter**: `record_type = 'station'`
- **Use case**: Station-level analysis (capacity, location, status)

#### 3. **wrm_bikes_only**
- **Description**: Filtered view showing only bike records
- **Filter**: `record_type = 'bike'`
- **Use case**: Individual bike tracking and movement analysis

#### 4. **wrm_stations_latest**
- **Description**: Latest snapshot of each station
- **Logic**: ROW_NUMBER() to get most recent record per station_id
- **Use case**: Current state analysis, real-time dashboards

### Analytics Capabilities
- **Station Summary Statistics**: Total records, latest station status
- **Spatial Density Analysis**: Bike distribution mapping
- **Temporal Analysis**: Historical trends and patterns
- **Real-time Monitoring**: Current station and bike availability

## 📁 Project Structure

```
bike-data-flow/
├── wrm_pipeline/
│   ├── assets/
│   │   ├── stations/
│   │   │   ├── __init__.py
│   │   │   ├── commons.py              # Shared utilities and partitions
│   │   │   ├── raw_all.py              # Raw data ingestion from API
│   │   │   ├── processed_all.py        # Data processing and validation
│   │   │   ├── enhanced_all.py         # Data enhancement and metadata
│   │   │   ├── enhanced_station_data.py # Station-only enhanced data
│   │   │   └── enhanced_bike_data.py   # Bike-only enhanced data
│   │   ├── duckdb/
│   │   │   ├── __init__.py
│   │   │   ├── create_enhanced_views.py # DuckDB view creation
│   │   │   ├── query_station_summary.py # Station summary queries
│   │   │   ├── bike_spatial_density_analysis.py # Spatial analysis
│   │   │   └── README.md               # DuckDB usage documentation
│   │   └── __init__.py
│   ├── models/
│   │   └── stations.py                 # Data models and schemas
│   └── config.py                       # Configuration management
└── README.md                           # Project documentation
```

## 🚀 Key Features

### **Intelligent Data Ingestion**
- Hash-based duplicate detection to avoid redundant API calls
- Automatic encoding issue detection and fixing
- Robust error handling and retry logic
- S3-based raw data persistence

### **Comprehensive Data Processing**
- Pydantic and Pandera-based data validation
- Type conversion and data cleaning
- Partition-aware processing for scalability
- Parquet optimization for analytical performance

### **Enhanced Data Management**
- Apache Iceberg support for advanced table management
- Metadata enrichment with processing timestamps
- Record type classification and filtering
- Daily partitioning for efficient data access

### **High-Performance Analytics**
- DuckDB-powered analytical views
- Direct S3 Parquet file querying
- Spatial analysis capabilities
- Real-time dashboard support

### **Data Quality Assurance**
- Schema validation at multiple pipeline stages
- Duplicate detection and handling
- Data lineage tracking
- Processing success monitoring

## 🔧 Configuration

Key configuration parameters are managed in [`config.py`](wrm_pipeline/wrm_pipeline/config.py):
- S3 bucket and prefix configurations
- API endpoints and credentials
- Processing parameters
- DuckDB connection settings

## 📊 Usage Examples

### DuckDB Analytics Queries
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

### Python Integration
```python
import duckdb
import os

db_path = os.path.join(os.path.expanduser("~"), "data", "analytics.duckdb")

with duckdb.connect(db_path) as conn:
    # Configure S3 credentials
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    # ... set credentials ...
    
    # Query views
    result = conn.execute("SELECT * FROM wrm_stations_latest LIMIT 5;").fetchall()
    print(result)
```

## 🚦 Getting Started

### 1. **Configure Environment**
- Set up S3-compatible storage credentials
- Configure API access parameters
- Set environment variables

### 2. **Deploy Pipeline**
```bash
# Install dependencies
pip install -r requirements.txt

# Launch Dagster UI
dagster-webserver -f wrm_pipeline
```

### 3. **Initialize Data Processing**
- Run initial data ingestion from WRM API
- Process and enhance historical data
- Create DuckDB analytical views

### 4. **Access Analytics**
- Use DuckDB CLI or Python for data analysis
- Query enhanced views for insights
- Build dashboards using latest station views

This pipeline provides a complete solution for bike-sharing data processing, from raw API ingestion to advanced analytics, with modern data engineering practices including schema validation, partitioning, and high-performance analytical capabilities.
