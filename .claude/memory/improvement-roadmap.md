# Dagster Pipeline Improvement Roadmap

**Last reviewed: 2026-01-09**

## Overview

This roadmap prioritizes improvements to the bike-data-flow Dagster pipeline based on 2024-2025 data engineering best practices. All recommendations are adapted for the Hetzner S3 storage and VPS compute environment constraints.

---

## Critical Priority (Weeks 1-2)

### 1. Secrets Management

**Problem**: Credentials stored in environment variables and `.env` files, committed to version control.

**Solution**: Implement secure secrets management compatible with Hetzner VPS.

**Options for Hetzner Environment**:

#### Option A: HashiCorp Vault (Recommended)
```bash
# Install Vault on Hetzner VPS
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install vault

# Configure Vault server
sudo systemctl start vault
```

**Dagster Integration**:
```python
from dagster import ConfigurableResource
import hvac

class VaultResource(ConfigurableResource):
    url: str
    token: str
    mount_point: str = "secret"

    def get_secret(self, path: str) -> dict:
        client = hvac.Client(url=self.url, token=self.token)
        return client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=self.mount_point
        )['data']['data']

# Usage in assets
@asset(required_resource_keys={"vault"})
def my_asset(context):
    s3_creds = context.resources.vault.get_secret("s3/credentials")
    # Use credentials
```

#### Option B: Environment Variables with Ansible Vault
```bash
# Encrypt secrets file
ansible-vault encrypt secrets.yml

# Decrypt at runtime
ansible-vault view secrets.yml
```

#### Option C: Docker Secrets (if using Docker)
```yaml
# docker-compose.yml
services:
  dagster:
    secrets:
      - s3_access_key
      - s3_secret_key
```

**Implementation Steps**:
1. Choose secrets management solution
2. Migrate existing credentials to vault
3. Update Dagster resources to use vault
4. Remove `.env` files from version control
5. Add `.env*` to `.gitignore`

**Estimated Effort**: 2-3 days

---

### 2. Retry Logic

**Problem**: Transient failures (network timeouts, S3 rate limits) cause pipeline failures.

**Solution**: Implement exponential backoff retry with circuit breaker pattern.

**Implementation**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests
from botocore.exceptions import ClientError

# Custom retry decorator for S3 operations
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ClientError, ConnectionError, TimeoutError)),
    reraise=True
)
def s3_upload_with_retry(s3_client, bucket, key, body, **kwargs):
    return s3_client.put_object(Bucket=bucket, Key=key, Body=body, **kwargs)

# Custom retry for API calls
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type((requests.exceptions.RequestException)),
    reraise=True
)
def fetch_api_with_retry(url, timeout=30):
    return requests.get(url, timeout=timeout)

# Circuit breaker for repeated failures
from circuitbreaker import circuit

@circuit(failure_threshold=5, recovery_timeout=60)
def external_api_call():
    # API call that may fail
    pass
```

**Apply to**:
- [`wrm_stations_raw_data_asset`](../../wrm_pipeline/wrm_pipeline/assets/stations/raw_all.py:23) - API fetch
- S3 upload operations in all assets
- S3 download operations

**Estimated Effort**: 1-2 days

---

### 3. Dead Letter Queue (DLQ)

**Problem**: Failed records are lost, no way to recover or analyze failures.

**Solution**: Implement DLQ for failed records in Hetzner S3.

**Implementation**:
```python
from dagster import asset, AssetExecutionContext, MaterializeResult
import pandas as pd
from datetime import datetime

@asset(
    name="wrm_stations_dlq",
    group_name="error_handling",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_dlq_asset(context: AssetExecutionContext) -> MaterializeResult:
    """Store failed records for later analysis and reprocessing"""
    s3_client = context.resources.s3_resource

    # DLQ structure
    dlq_record = {
        "timestamp": datetime.now().isoformat(),
        "asset_name": "wrm_stations_processed_data_all",
        "partition_date": context.partition_key,
        "error_type": "validation_error",
        "error_message": "Schema validation failed",
        "raw_data": "...",  # Original data that failed
        "stack_trace": "...",
        "retry_count": 0,
        "status": "pending_reprocessing"
    }

    # Upload to DLQ in S3
    dlq_key = f"{WRM_STATIONS_S3_PREFIX}dlq/{datetime.now().strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=dlq_key,
        Body=json.dumps(dlq_record).encode('utf-8'),
        ContentType='application/json'
    )

    return MaterializeResult(
        metadata={
            "dlq_key": dlq_key,
            "error_type": dlq_record["error_type"],
            "status": dlq_record["status"]
        }
    )

# Reprocessing job
@asset_job(name="reprocess_dlq")
def reprocess_dlq_job():
    """Reprocess records from DLQ"""
    pass
```

**DLQ Structure in S3**:
```
bike-data/gen_info/dlq/
└── dt=YYYY-MM-DD/
    └── {uuid}.json
```

**Estimated Effort**: 2-3 days

---

### 4. Unit Tests

**Problem**: No test coverage for assets, jobs, or sensors.

**Solution**: Implement comprehensive unit test suite.

**Test Structure**:
```
wrm_pipeline/wrm_pipeline_tests/
├── unit/
│   ├── assets/
│   │   ├── test_raw_all.py
│   │   ├── test_processed_all.py
│   │   └── test_enhanced_all.py
│   ├── jobs/
│   │   └── test_stations.py
│   ├── sensors/
│   │   └── test_stations.py
│   └── resources/
│       └── test_resources.py
├── integration/
│   └── test_pipeline.py
└── fixtures/
    └── sample_data/
```

**Example Test**:
```python
import pytest
from unittest.mock import Mock, patch
from wrm_pipeline.assets.stations.raw_all import wrm_stations_raw_data_asset
from dagster import build_asset_context

@pytest.fixture
def mock_s3_client():
    with patch('wrm_pipeline.assets.stations.raw_all.requests') as mock_requests:
        mock_response = Mock()
        mock_response.text = "sample,data"
        mock_response.raise_for_status = Mock()
        mock_requests.get.return_value = mock_response
        yield mock_requests

def test_wrm_stations_raw_data_asset(mock_s3_client):
    context = build_asset_context(resources={"s3_resource": Mock()})
    result = wrm_stations_raw_data_asset(context)
    assert result is not None
```

**Estimated Effort**: 3-5 days

---

## High Priority (Weeks 3-4)

### 5. Caching Layer

**Problem**: Repeated S3 calls for same data, no caching of intermediate results.

**Solution**: Implement caching with Redis or local filesystem.

**Option A: Redis (Recommended for VPS)**
```bash
# Install Redis on Hetzner VPS
sudo apt install redis-server
sudo systemctl start redis
```

**Dagster Integration**:
```python
from dagster import ConfigurableResource
import redis

class RedisCacheResource(ConfigurableResource):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    ttl: int = 3600  # 1 hour default

    def get_client(self):
        return redis.Redis(host=self.host, port=self.port, db=self.db)

    def get(self, key: str):
        client = self.get_client()
        value = client.get(key)
        return json.loads(value) if value else None

    def set(self, key: str, value: dict):
        client = self.get_client()
        client.setex(key, self.ttl, json.dumps(value))

# Usage in assets
@asset(required_resource_keys={"redis_cache", "s3_resource"})
def cached_asset(context):
    cache_key = f"asset:{context.asset_key.to_user_string()}:{context.partition_key}"
    cached = context.resources.redis_cache.get(cache_key)
    if cached:
        context.log.info("Returning cached result")
        return cached

    # Compute result
    result = compute_expensive_operation()

    # Cache result
    context.resources.redis_cache.set(cache_key, result)
    return result
```

**Option B: Local Filesystem Cache**
```python
import hashlib
import pickle
from pathlib import Path

class FilesystemCacheResource(ConfigurableResource):
    cache_dir: str = "/tmp/dagster_cache"

    def get_cache_path(self, key: str) -> Path:
        key_hash = hashlib.md5(key.encode()).hexdigest()
        return Path(self.cache_dir) / key_hash[:2] / key_hash

    def get(self, key: str):
        path = self.get_cache_path(key)
        if path.exists():
            with open(path, 'rb') as f:
                return pickle.load(f)
        return None

    def set(self, key: str, value):
        path = self.get_cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            pickle.dump(value, f)
```

**Estimated Effort**: 2-3 days

---

### 6. Business Logic Validation

**Problem**: Only schema validation, no business rules enforced.

**Solution**: Implement business logic validation layer.

**Implementation**:
```python
from pandera import Check, Column
from typing import List

class BusinessLogicValidator:
    """Business rules for bike station data"""

    @staticmethod
    def validate_station_coordinates(df: pd.DataFrame) -> pd.DataFrame:
        """Validate coordinates are within Wrocław bounds"""
        WROCLAW_BOUNDS = {
            "lat_min": 51.0,
            "lat_max": 51.2,
            "lon_min": 16.8,
            "lon_max": 17.2
        }

        out_of_bounds = df[
            (df['lat'] < WROCLAW_BOUNDS['lat_min']) |
            (df['lat'] > WROCLAW_BOUNDS['lat_max']) |
            (df['lon'] < WROCLAW_BOUNDS['lon_min']) |
            (df['lon'] > WROCLAW_BOUNDS['lon_max'])
        ]

        if not out_of_bounds.empty:
            raise ValueError(
                f"Found {len(out_of_bounds)} records with coordinates "
                f"outside Wrocław bounds"
            )

        return df

    @staticmethod
    def validate_bike_counts(df: pd.DataFrame) -> pd.DataFrame:
        """Validate bike counts are non-negative and don't exceed total docks"""
        invalid = df[
            (df['bikes'] < 0) |
            (df['spaces'] < 0) |
            (df['bikes'] + df['spaces'] > df['total_docks'])
        ]

        if not invalid.empty:
            raise ValueError(
                f"Found {len(invalid)} records with invalid bike counts"
            )

        return df

    @staticmethod
    def validate_timestamp_sequence(df: pd.DataFrame) -> pd.DataFrame:
        """Validate timestamps are in chronological order"""
        if not df['timestamp'].is_monotonic_increasing:
            raise ValueError("Timestamps are not in chronological order")
        return df

# Enhanced schema with business logic
enhanced_business_schema = DataFrameSchema({
    "lat": Column(float, Check.between(51.0, 51.2)),
    "lon": Column(float, Check.between(16.8, 17.2)),
    "bikes": Column(int, Check.ge(0)),
    "spaces": Column(int, Check.ge(0)),
})

# Usage in assets
@asset
def validated_asset(context, df: pd.DataFrame) -> pd.DataFrame:
    validator = BusinessLogicValidator()
    df = validator.validate_station_coordinates(df)
    df = validator.validate_bike_counts(df)
    df = validator.validate_timestamp_sequence(df)
    return df
```

**Estimated Effort**: 2-3 days

---

### 7. Structured Logging

**Problem**: Basic logging without request tracing or correlation IDs.

**Solution**: Implement structured logging with correlation IDs.

**Implementation**:
```python
import structlog
from uuid import uuid4

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

# Dagster integration
class StructlogResource(ConfigurableResource):
    def get_logger(self, **kwargs):
        log = structlog.get_logger()
        # Add correlation ID
        log = log.bind(correlation_id=str(uuid4()))
        # Add context
        for key, value in kwargs.items():
            log = log.bind(**{key: value})
        return log

# Usage in assets
@asset(required_resource_keys={"structlog"})
def logged_asset(context):
    log = context.resources.structlog.get_logger(
        asset_name=context.asset_key.to_user_string(),
        partition_key=context.partition_key
    )

    log.info("Starting asset computation")

    try:
        result = compute_operation()
        log.info("Asset computation completed", record_count=len(result))
        return result
    except Exception as e:
        log.error("Asset computation failed", error=str(e))
        raise
```

**Estimated Effort**: 1-2 days

---

### 8. CI/CD Pipeline

**Problem**: Manual deployment process, no automated testing.

**Solution**: Implement CI/CD with GitHub Actions.

**GitHub Actions Workflow**:
```yaml
# .github/workflows/dagster-ci.yml
name: Dagster CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -e .
          pip install pytest pytest-cov

      - name: Run unit tests
        run: pytest wrm_pipeline/wrm_pipeline_tests/unit/ --cov=wrm_pipeline --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v3

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v3

      - name: Deploy to Hetzner VPS
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.HETZNER_HOST }}
          username: ${{ secrets.HETZNER_USER }}
          key: ${{ secrets.HETZNER_SSH_KEY }}
          script: |
            cd /opt/bike-data-flow
            git pull origin main
            pip install -e .
            systemctl restart dagster-daemon
```

**Estimated Effort**: 2-3 days

---

## Medium Priority (Weeks 5-6)

### 9. Monitoring & Observability

**Problem**: No metrics collection or dashboards.

**Solution**: Implement monitoring with Prometheus and Grafana.

**Option A: Prometheus + Grafana (Recommended for VPS)**
```bash
# Install Prometheus on Hetzner VPS
wget https://github.com/prometheus/prometheus/releases/download/v2.47.0/prometheus-2.47.0.linux-amd64.tar.gz
tar xvfz prometheus-2.47.0.linux-amd64.tar.gz
cd prometheus-2.47.0.linux-amd64
./prometheus --config.file=prometheus.yml

# Install Grafana
sudo apt install -y software-properties-common
sudo add-apt-repository "deb https://packages.grafana.com/oss/deb stable main"
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -
sudo apt update
sudo apt install grafana
sudo systemctl start grafana-server
```

**Dagster Metrics Integration**:
```python
from dagster import asset, AssetExecutionContext
from prometheus_client import Counter, Histogram, start_http_server

# Define metrics
asset_runs_total = Counter(
    'dagster_asset_runs_total',
    'Total number of asset runs',
    ['asset_name', 'status']
)

asset_run_duration = Histogram(
    'dagster_asset_run_duration_seconds',
    'Asset run duration in seconds',
    ['asset_name']
)

@asset
def monitored_asset(context: AssetExecutionContext):
    asset_name = context.asset_key.to_user_string()

    with asset_run_duration.labels(asset_name=asset_name).time():
        try:
            result = compute_operation()
            asset_runs_total.labels(asset_name=asset_name, status='success').inc()
            return result
        except Exception as e:
            asset_runs_total.labels(asset_name=asset_name, status='failure').inc()
            raise
```

**Prometheus Configuration**:
```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'dagster'
    static_configs:
      - targets: ['localhost:8000']
```

**Estimated Effort**: 3-4 days

---

### 10. Parallel Processing

**Problem**: Sequential processing of files, slow for large datasets.

**Solution**: Implement parallel processing with multiprocessing or Dask.

**Option A: Multiprocessing (Simple)**
```python
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial

def process_single_file(file_info, s3_client, bucket):
    """Process a single file"""
    # Download and process file
    response = s3_client.get_object(Bucket=bucket, Key=file_info['Key'])
    data = response['Body'].read()
    # Process data
    return processed_data

@asset
def parallel_processed_asset(context: AssetExecutionContext):
    s3_client = context.resources.s3_resource

    # List all files
    response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)
    files = response['Contents']

    # Process in parallel
    with ProcessPoolExecutor(max_workers=4) as executor:
        process_func = partial(process_single_file, s3_client=s3_client, bucket=BUCKET_NAME)
        results = list(executor.map(process_func, files))

    # Combine results
    return pd.concat(results)
```

**Option B: Dask (For larger datasets)**
```python
import dask.dataframe as dd
import dask.bag as db

@asset
def dask_processed_asset(context: AssetExecutionContext):
    # Create Dask bag from S3 files
    bag = db.read_text('s3://bucket/prefix/*.txt',
                       storage_options={
                           'key': S3_ACCESS_KEY_ID,
                           'secret': S3_SECRET_ACCESS_KEY,
                           'client_kwargs': {'endpoint_url': S3_ENDPOINT_URL}
                       })

    # Process in parallel
    processed = bag.map(process_line).compute()

    return pd.DataFrame(processed)
```

**Estimated Effort**: 2-3 days

---

### 11. Infrastructure as Code

**Problem**: Manual resource configuration, no reproducibility.

**Solution**: Implement IaC with Ansible or Terraform.

**Option A: Ansible (Recommended for Hetzner VPS)**
```yaml
# playbook.yml
---
- name: Configure Dagster Pipeline Server
  hosts: all
  become: yes
  vars:
    dagster_user: dagster
    dagster_home: /opt/dagster

  tasks:
    - name: Install system dependencies
      apt:
        name:
          - python3
          - python3-pip
          - redis-server
          - git
        state: present

    - name: Create dagster user
      user:
        name: "{{ dagster_user }}"
        system: yes
        home: "{{ dagster_home }}"

    - name: Clone repository
      git:
        repo: https://github.com/your-repo/bike-data-flow.git
        dest: "{{ dagster_home }}/bike-data-flow"
        version: main

    - name: Install Python dependencies
      pip:
        requirements: "{{ dagster_home }}/bike-data-flow/requirements.txt"
        executable: pip3

    - name: Configure systemd service
      template:
        src: dagster-daemon.service.j2
        dest: /etc/systemd/system/dagster-daemon.service
      notify: Restart Dagster

    - name: Start Dagster daemon
      systemd:
        name: dagster-daemon
        state: started
        enabled: yes

  handlers:
    - name: Restart Dagster
      systemd:
        name: dagster-daemon
        state: restarted
```

**Estimated Effort**: 2-3 days

---

## Low Priority (Weeks 7+)

### 12. Materialized Views

**Problem**: Expensive queries recompute each time.

**Solution**: Implement materialized views in DuckDB.

**Implementation**:
```python
@asset
def create_materialized_views(context: AssetExecutionContext):
    """Create materialized views for common queries"""
    conn = get_duckdb_connection()

    # Station latest view
    conn.execute("""
        CREATE OR REPLACE MATERIALIZED VIEW mv_station_latest AS
        SELECT
            station_id,
            name,
            lat,
            lon,
            bikes,
            spaces,
            total_docks,
            timestamp
        FROM wrm_stations_enhanced
        WHERE record_type = 'station'
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY station_id ORDER BY timestamp DESC
        ) = 1
    """)

    # Daily aggregates view
    conn.execute("""
        CREATE OR REPLACE MATERIALIZED VIEW mv_daily_aggregates AS
        SELECT
            date,
            COUNT(DISTINCT station_id) as active_stations,
            AVG(bikes) as avg_bikes,
            MAX(bikes) as max_bikes,
            MIN(bikes) as min_bikes
        FROM wrm_stations_enhanced
        WHERE record_type = 'station'
        GROUP BY date
    """)

    # Refresh strategy
    conn.execute("REFRESH MATERIALIZED VIEW mv_station_latest")
    conn.execute("REFRESH MATERIALIZED VIEW mv_daily_aggregates")
```

**Estimated Effort**: 1-2 days

---

### 13. Data Lineage

**Problem**: No tracking of data transformations.

**Solution**: Implement data lineage tracking.

**Implementation**:
```python
from dagster import asset, AssetExecutionContext, MetadataValue
import json

class LineageTracker:
    """Track data lineage through the pipeline"""

    def __init__(self, context: AssetExecutionContext):
        self.context = context
        self.lineage = {
            "asset_name": context.asset_key.to_user_string(),
            "run_id": context.run_id,
            "timestamp": datetime.now().isoformat(),
            "upstream_assets": [],
            "transformations": [],
            "output_records": 0,
            "output_bytes": 0
        }

    def add_upstream(self, asset_key: str, record_count: int):
        self.lineage["upstream_assets"].append({
            "asset_name": asset_key,
            "record_count": record_count
        })

    def add_transformation(self, name: str, description: str):
        self.lineage["transformations"].append({
            "name": name,
            "description": description
        })

    def finalize(self, df: pd.DataFrame):
        self.lineage["output_records"] = len(df)
        self.lineage["output_bytes"] = df.memory_usage(deep=True).sum()

        # Store lineage in S3
        lineage_key = f"{WRM_STATIONS_S3_PREFIX}lineage/{datetime.now().strftime('%Y-%m-%d')}/{self.context.run_id}.json"
        s3_client = self.context.resources.s3_resource
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=lineage_key,
            Body=json.dumps(self.lineage).encode('utf-8')
        )

        return self.lineage

# Usage in assets
@asset
def lineage_tracked_asset(context: AssetExecutionContext, upstream_df: pd.DataFrame):
    tracker = LineageTracker(context)
    tracker.add_upstream("upstream_asset", len(upstream_df))

    tracker.add_transformation("filter", "Filter invalid records")
    df = upstream_df[upstream_df['valid'] == True]

    tracker.add_transformation("aggregate", "Aggregate by station")
    df = df.groupby('station_id').agg({'bikes': 'mean'}).reset_index()

    lineage = tracker.finalize(df)
    context.add_output_metadata({"lineage": MetadataValue.json(lineage)})

    return df
```

**Estimated Effort**: 2-3 days

---

### 14. Advanced Data Quality

**Problem**: No anomaly detection or data profiling.

**Solution**: Implement advanced DQ checks.

**Implementation**:
```python
import great_expectations as gx
from great_expectations.core import ExpectationSuite, ExpectationConfiguration

class AdvancedDataQuality:
    """Advanced data quality checks"""

    def __init__(self, context: AssetExecutionContext):
        self.context = context
        self.context = gx.get_context()

    def create_expectation_suite(self, suite_name: str):
        suite = ExpectationSuite(expectation_suite_name=suite_name)
        suite.add_expectation(
            ExpectationConfiguration(
                expectation_type="expect_column_values_to_be_between",
                kwargs={
                    "column": "lat",
                    "min_value": 51.0,
                    "max_value": 51.2
                }
            )
        )
        suite.add_expectation(
            ExpectationConfiguration(
                expectation_type="expect_column_values_to_not_be_null",
                kwargs={"column": "station_id"}
            )
        )
        suite.add_expectation(
            ExpectationConfiguration(
                expectation_type="expect_column_values_to_be_unique",
                kwargs={"column": ["station_id", "timestamp"]}
            )
        )
        return suite

    def validate_data(self, df: pd.DataFrame, suite: ExpectationSuite):
        batch = gx.dataset.PandasDataset(df)
        results = batch.validate(suite)

        if not results.success:
            self.context.log.error(f"Data quality validation failed: {results}")

        return results

# Anomaly detection
from sklearn.ensemble import IsolationForest

def detect_anomalies(df: pd.DataFrame, contamination: float = 0.01):
    """Detect anomalies in bike counts"""
    model = IsolationForest(contamination=contamination, random_state=42)
    features = df[['bikes', 'spaces', 'total_docks']]
    df['anomaly'] = model.fit_predict(features)
    df['anomaly_score'] = model.score_samples(features)

    anomalies = df[df['anomaly'] == -1]
    return anomalies
```

**Estimated Effort**: 3-4 days

---

## Implementation Priority Summary

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| Critical | Secrets Management | 2-3 days | High |
| Critical | Retry Logic | 1-2 days | High |
| Critical | Dead Letter Queue | 2-3 days | High |
| Critical | Unit Tests | 3-5 days | High |
| High | Caching Layer | 2-3 days | Medium |
| High | Business Logic Validation | 2-3 days | Medium |
| High | Structured Logging | 1-2 days | Medium |
| High | CI/CD Pipeline | 2-3 days | High |
| Medium | Monitoring & Observability | 3-4 days | High |
| Medium | Parallel Processing | 2-3 days | Medium |
| Medium | Infrastructure as Code | 2-3 days | High |
| Low | Materialized Views | 1-2 days | Low |
| Low | Data Lineage | 2-3 days | Low |
| Low | Advanced Data Quality | 3-4 days | Medium |

---

## Related Memory Files

- [`pipeline-analysis.md`](pipeline-analysis.md) - Current pipeline architecture
- [`data-engineering-best-practices.md`](data-engineering-best-practices.md) - Best practices research
- [`infrastructure-constraints.md`](infrastructure-constraints.md) - Hetzner S3/VPS constraints

---

*Last Updated: 2026-01-09*
*Roadmap Phase: Complete*
