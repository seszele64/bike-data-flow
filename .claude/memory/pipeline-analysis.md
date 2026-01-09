# Dagster Pipeline Analysis - Bike Data Flow

**Last reviewed: 2026-01-09**

## Overview

The bike-data-flow project uses Dagster as its orchestration framework for processing WRM (Wrocław) bike station data. The pipeline follows an event-driven architecture with sensor-based triggering and daily partitioned data processing.

## Current Architecture

### Pipeline Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DAGSTER PIPELINE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   SENSOR    │───▶│     JOB     │───▶│   ASSETS    │───▶│  RESOURCES  │  │
│  │  (30s poll) │    │  (trigger)  │    │  (process)  │    │  (storage)  │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Assets (7 total)

#### Data Acquisition Assets
1. **`wrm_stations_raw_data`** ([`raw_all.py`](../../wrm_pipeline/wrm_pipeline/assets/stations/raw_all.py:23))
   - Fetches raw station data from WRM API
   - Stores as `.txt` files in S3 with Hive-style partitioning
   - Implements duplicate detection using SHA-256 hashing
   - No partitioning (fetches current state)

#### Processing Assets
2. **`wrm_stations_processed_data_all`** ([`processed_all.py`](../../wrm_pipeline/wrm_pipeline/assets/stations/processed_all.py:29))
   - Daily partitioned asset
   - Parses raw CSV data with timestamp splitting
   - Converts data types (int, float, boolean)
   - Validates against Pandera schema
   - Combines multiple raw files per partition

3. **`wrm_stations_enhanced_data_all`** ([`enhanced_all.py`](../../wrm_pipeline/wrm_pipeline/assets/stations/enhanced_all.py:29))
   - Daily partitioned asset
   - Depends on processed data
   - Adds record type classification (station/bike/unknown)
   - Validates against enhanced schema
   - Stores as Parquet in S3

#### DuckDB Analytics Assets
4. **`create_duckdb_enhanced_views`** ([`duckdb/create_enhanced_views.py`](../../wrm_pipeline/wrm_pipeline/assets/duckdb/create_enhanced_views.py))
   - Creates DuckDB views for analytics
   - Enables efficient querying of processed data

5. **`query_station_summary`** ([`duckdb/query_station_summary.py`](../../wrm_pipeline/wrm_pipeline/assets/duckdb/query_station_summary.py))
   - Provides station-level summary statistics

6. **`bike_density_spatial_analysis`** ([`duckdb/bike_spatial_density_analysis.py`](../../wrm_pipeline/wrm_pipeline/assets/duckdb/bike_spatial_density_analysis.py))
   - Spatial analysis of bike distribution
   - Generates density metrics

7. **`bike_density_map`** ([`duckdb/bike_spatial_density_analysis.py`](../../wrm_pipeline/wrm_pipeline/assets/duckdb/bike_spatial_density_analysis.py))
   - Creates map visualization data

### Jobs (1 total)

**`wrm_stations_processing_job`** ([`stations.py`](../../wrm_pipeline/wrm_pipeline/jobs/stations.py:6))
- Materializes processed and enhanced data assets
- Triggered by sensor when new raw data arrives
- Processes data by date partition

### Sensors (1 total)

**`wrm_stations_raw_data_sensor`** ([`stations.py`](../../wrm_pipeline/wrm_pipeline/sensors/stations.py:13))
- Polls S3 every 30 seconds for new raw data files
- Groups new files by date partition
- Creates separate run requests per partition
- Maintains cursor state for incremental processing

### Resources (6 total)

1. **`s3_resource`** - S3 client for storage operations
2. **`postgres_resource`** - PostgreSQL connection (currently unused)
3. **`duckdb_io_manager`** - Local DuckDB I/O manager
4. **`duckdb_s3_io_manager`** - DuckDB with S3 integration
5. **`duckdb_hybrid_io_manager`** - Hybrid local/S3 DuckDB
6. **`s3_io_manager`** - S3 pickle I/O manager
7. **`hive_partitioned_s3_io_manager`** - Custom Hive-style partitioning

## Data Flow

```
┌──────────────┐
│  WRM API     │
│  (external)  │
└──────┬───────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  wrm_stations_raw_data                                     │
│  - Fetches current station data                             │
│  - Stores as .txt in S3: raw/dt=YYYY-MM-DD/                │
│  - Duplicate detection via SHA-256 hash                    │
└──────┬──────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  wrm_stations_raw_data_sensor                              │
│  - Polls S3 every 30s                                      │
│  - Detects new .txt files                                  │
│  - Groups by date partition                                │
│  - Triggers processing job                                 │
└──────┬──────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  wrm_stations_processing_job                               │
│  - Materializes processed and enhanced assets              │
│  - Runs per date partition                                 │
└──────┬──────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  wrm_stations_processed_data_all                           │
│  - Parses CSV with timestamp splitting                      │
│  - Type conversion (int, float, bool)                      │
│  - Pandera schema validation                               │
│  - Stores as Parquet in S3: processed/all/dt=YYYY-MM-DD/  │
└──────┬──────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  wrm_stations_enhanced_data_all                           │
│  - Classifies records (station/bike/unknown)               │
│  - Enhanced schema validation                               │
│  - Stores as Parquet in S3: enhanced/all/dt=YYYY-MM-DD/    │
└──────┬──────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  DuckDB Analytics Assets                                   │
│  - create_duckdb_enhanced_views                            │
│  - query_station_summary                                   │
│  - bike_density_spatial_analysis                           │
│  - bike_density_map                                        │
└─────────────────────────────────────────────────────────────┘
```

## Storage Structure

### S3/Hetzner Storage Layout

```
bike-data/
└── gen_info/
    ├── raw/
    │   └── dt=YYYY-MM-DD/
    │       └── wrm_stations_YYYY-MM-DD_HH-MM-SS.txt
    ├── processed/
    │   └── all/
    │       └── dt=YYYY-MM-DD/
    │           └── all_processed_YYYYMMDD_HHMMSS.parquet
    └── enhanced/
        └── all/
            └── dt=YYYY-MM-DD/
                └── all_enhanced_YYYYMMDD_HHMMSS.parquet
```

### DuckDB Storage

- Local: `~/data/analytics.duckdb`
- Schema: `wrm_analytics`
- S3 Integration: `s3://{bucket}/bike-data/gen_info/duckdb/analytics.duckdb`

## Strengths

1. **Event-Driven Architecture**: Sensor-based triggering reduces unnecessary processing
2. **Hive-Style Partitioning**: Organized storage with date-based partitions
3. **Schema Validation**: Pandera schemas ensure data quality
4. **Duplicate Detection**: SHA-256 hashing prevents redundant storage
5. **Multiple Storage Options**: Local DuckDB, S3, and hybrid approaches
6. **Metadata Tracking**: Comprehensive metadata for each asset
7. **Type Safety**: Pydantic models for configuration

## Weaknesses & Gaps

### Critical Gaps
1. **No Secrets Management**: Credentials stored in environment variables and `.env` files
2. **No Retry Logic**: Transient failures cause pipeline failures
3. **No Dead Letter Queue**: Failed records are lost
4. **No Unit Tests**: No test coverage for assets, jobs, or sensors
5. **No Integration Tests**: No end-to-end pipeline testing

### High Priority Gaps
6. **No Caching**: Repeated S3 calls for same data
7. **No Business Logic Validation**: Only schema validation, no business rules
8. **No Structured Logging**: Basic logging without request tracing
9. **No CI/CD**: Manual deployment process
10. **No Error Alerts**: No notification system for failures

### Medium Priority Gaps
11. **No Monitoring**: No metrics collection or dashboards
12. **No Parallel Processing**: Sequential processing of files
13. **No Infrastructure as Code**: Manual resource configuration
14. **No Data Lineage**: No tracking of data transformations

### Low Priority Gaps
15. **No Materialized Views**: Expensive queries recompute each time
16. **No Advanced Data Quality**: No anomaly detection or profiling
17. **No Data Catalog**: No searchable metadata repository

## Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Orchestration | Dagster | Latest |
| Data Processing | Pandas | Latest |
| Validation | Pandera | Latest |
| Storage | Hetzner S3 (MinIO-compatible) | - |
| Analytics | DuckDB | Latest |
| HTTP Client | Requests | Latest |
| Encoding Fix | ftfy | Latest |
| Schema | Pydantic | Latest |

## Configuration

### Environment Variables

```python
# S3/Hetzner Configuration
S3_ENDPOINT_URL
S3_ACCESS_KEY_ID
S3_SECRET_ACCESS_KEY
S3_REGION_NAME
BUCKET_NAME
WRM_STATIONS_S3_PREFIX

# Hetzner-specific
HETZNER_ENDPOINT_URL
HETZNER_ACCESS_KEY_ID
HETZNER_SECRET_ACCESS_KEY

# PostgreSQL (currently unused)
POSTGRES_HOST
POSTGRES_PORT
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD

# MinIO (alternative storage)
MINIO_ENDPOINT_URL
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
```

## Key Files

| File | Purpose |
|------|---------|
| [`definitions.py`](../../wrm_pipeline/wrm_pipeline/definitions.py) | Dagster definitions entry point |
| [`resources.py`](../../wrm_pipeline/wrm_pipeline/resources.py) | Resource configurations |
| [`assets/assets.py`](../../wrm_pipeline/wrm_pipeline/assets/assets.py) | Asset aggregator |
| [`jobs/stations.py`](../../wrm_pipeline/wrm_pipeline/jobs/stations.py) | Job definitions |
| [`sensors/stations.py`](../../wrm_pipeline/wrm_pipeline/sensors/stations.py) | Sensor definitions |
| [`models/stations.py`](../../wrm_pipeline/wrm_pipeline/models/stations.py) | Pandera schemas |

## Related Memory Files

- [`improvement-roadmap.md`](improvement-roadmap.md) - Prioritized improvements
- [`data-engineering-best-practices.md`](data-engineering-best-practices.md) - Best practices research
- [`infrastructure-constraints.md`](infrastructure-constraints.md) - Hetzner S3/VPS constraints

---

*Last Updated: 2026-01-09*
*Analysis Phase: Complete*
