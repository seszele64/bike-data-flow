# Data Engineering Best Practices (2024-2025)

**Last reviewed: 2026-01-09**

## Overview

This document summarizes key data engineering best practices based on 2024-2025 research and industry standards. These practices are applicable to the bike-data-flow Dagster pipeline and can be adapted for Hetzner S3/VPS environments.

---

## 1. Monitoring and Observability

### The 5 Pillars of Observability

#### 1.1 Metrics (Quantitative Data)

**What to Track**:
- **Pipeline Health**: Success/failure rates, run duration, queue depth
- **Data Quality**: Record counts, null percentages, schema violations
- **Performance**: Throughput (records/sec), latency, resource utilization
- **Business Metrics**: Data freshness, SLA compliance, user impact

**Implementation with Prometheus**:
```python
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Pipeline metrics
pipeline_runs_total = Counter(
    'dagster_pipeline_runs_total',
    'Total pipeline runs',
    ['pipeline_name', 'status']
)

pipeline_duration_seconds = Histogram(
    'dagster_pipeline_duration_seconds',
    'Pipeline run duration',
    ['pipeline_name'],
    buckets=[60, 300, 600, 1800, 3600]  # 1min, 5min, 10min, 30min, 1hr
)

active_jobs_gauge = Gauge(
    'dagster_active_jobs',
    'Number of active jobs'
)

# Data quality metrics
record_count_gauge = Gauge(
    'dagster_asset_record_count',
    'Number of records in asset',
    ['asset_name']
)

null_percentage_gauge = Gauge(
    'dagster_asset_null_percentage',
    'Percentage of null values',
    ['asset_name', 'column_name']
)

# Usage in assets
@asset
def monitored_asset(context: AssetExecutionContext):
    asset_name = context.asset_key.to_user_string()

    with pipeline_duration_seconds.labels(pipeline_name=asset_name).time():
        try:
            result = compute_operation()

            # Record metrics
            record_count_gauge.labels(asset_name=asset_name).set(len(result))

            # Calculate null percentages
            for col in result.columns:
                null_pct = (result[col].isnull().sum() / len(result)) * 100
                null_percentage_gauge.labels(
                    asset_name=asset_name,
                    column_name=col
                ).set(null_pct)

            pipeline_runs_total.labels(
                pipeline_name=asset_name,
                status='success'
            ).inc()

            return result

        except Exception as e:
            pipeline_runs_total.labels(
                pipeline_name=asset_name,
                status='failure'
            ).inc()
            raise
```

#### 1.2 Logs (Discrete Events)

**Structured Logging Best Practices**:
```python
import structlog
from uuid import uuid4

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

# Usage with correlation IDs
def get_logger(context: AssetExecutionContext):
    log = structlog.get_logger()
    return log.bind(
        correlation_id=str(uuid4()),
        asset_name=context.asset_key.to_user_string(),
        run_id=context.run_id,
        partition_key=context.partition_key
    )

# In assets
@asset
def logged_asset(context: AssetExecutionContext):
    log = get_logger(context)

    log.info("Starting asset computation")

    try:
        result = compute_operation()
        log.info("Asset computation completed", record_count=len(result))
        return result
    except Exception as e:
        log.error("Asset computation failed", error=str(e), error_type=type(e).__name__)
        raise
```

**Log Levels**:
- **DEBUG**: Detailed diagnostic information
- **INFO**: General informational messages
- **WARNING**: Unexpected situations that don't prevent operation
- **ERROR**: Errors that prevent operation but allow recovery
- **CRITICAL**: Severe errors that require immediate attention

#### 1.3 Traces (Request Flow)

**Distributed Tracing with OpenTelemetry**:
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter

# Configure tracing
trace.set_tracer_provider(TracerProvider())
jaeger_exporter = JaegerExporter(
    agent_host_name="localhost",
    agent_port=6831,
)
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(jaeger_exporter)
)

tracer = trace.get_tracer(__name__)

# Usage in assets
@asset
def traced_asset(context: AssetExecutionContext):
    with tracer.start_as_current_span("process_asset") as span:
        span.set_attribute("asset.name", context.asset_key.to_user_string())
        span.set_attribute("asset.run_id", context.run_id)

        with tracer.start_as_current_span("fetch_data"):
            data = fetch_data()

        with tracer.start_as_current_span("transform_data"):
            result = transform_data(data)

        return result
```

#### 1.4 Alerts (Notifications)

**Alerting Best Practices**:
- **Alert on symptoms, not causes**: Alert on "high error rate" not "database down"
- **Use meaningful thresholds**: Based on SLAs, not arbitrary values
- **Include actionable context**: What happened, where, and what to do
- **Avoid alert fatigue**: Only alert on actionable issues

**Alert Configuration**:
```yaml
# Prometheus alert rules
groups:
  - name: dagster_alerts
    rules:
      - alert: HighPipelineFailureRate
        expr: rate(dagster_pipeline_runs_total{status="failure"}[5m]) > 0.1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High pipeline failure rate"
          description: "Pipeline {{ $labels.pipeline_name }} has >10% failure rate"

      - alert: DataFreshnessSLABreach
        expr: time() - dagster_asset_last_success_timestamp > 3600
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Data freshness SLA breach"
          description: "Asset {{ $labels.asset_name }} hasn't updated in 1 hour"
```

#### 1.5 Dashboards (Visualization)

**Key Dashboard Components**:
1. **Pipeline Health Overview**: Success rate, active jobs, queue depth
2. **Asset Performance**: Run duration, record counts, data freshness
3. **Data Quality**: Null percentages, schema violations, anomaly counts
4. **Resource Utilization**: CPU, memory, disk I/O, network
5. **Business Metrics**: SLA compliance, user impact, data volume

---

## 2. Error Handling Patterns

### 2.1 Retry with Exponential Backoff

**Implementation**:
```python
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
import logging

logger = logging.getLogger(__name__)

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type((
        requests.exceptions.RequestException,
        ConnectionError,
        TimeoutError
    )),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True
)
def fetch_api_with_retry(url: str, timeout: int = 30):
    """Fetch API with exponential backoff retry"""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()
```

**Best Practices**:
- Start with short delays (1s) and increase exponentially
- Set maximum delay to avoid excessive wait times
- Log retry attempts for debugging
- Only retry on transient errors (network timeouts, rate limits)
- Don't retry on permanent errors (404, 403, 500)

### 2.2 Dead Letter Queue (DLQ)

**Pattern**:
```python
from dagster import asset, AssetExecutionContext, MaterializeResult
import json
from datetime import datetime
import uuid

@asset(
    name="processing_dlq",
    group_name="error_handling",
    required_resource_keys={"s3_resource"}
)
def dead_letter_queue_asset(
    context: AssetExecutionContext,
    failed_records: list
) -> MaterializeResult:
    """Store failed records for later reprocessing"""

    dlq_records = []
    for record in failed_records:
        dlq_record = {
            "record_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "asset_name": context.asset_key.to_user_string(),
            "partition_key": context.partition_key,
            "run_id": context.run_id,
            "error_type": record.get("error_type"),
            "error_message": record.get("error_message"),
            "raw_data": record.get("raw_data"),
            "stack_trace": record.get("stack_trace"),
            "retry_count": 0,
            "max_retries": 3,
            "status": "pending_reprocessing"
        }
        dlq_records.append(dlq_record)

    # Upload to S3
    s3_client = context.resources.s3_resource
    dlq_key = f"dlq/{datetime.now().strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"

    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=dlq_key,
        Body=json.dumps(dlq_records).encode('utf-8'),
        ContentType='application/json'
    )

    return MaterializeResult(
        metadata={
            "dlq_key": dlq_key,
            "failed_records_count": len(dlq_records),
            "status": "queued_for_reprocessing"
        }
    )
```

**DLQ Reprocessing Job**:
```python
from dagster import job, op

@op
def fetch_dlq_records(context):
    """Fetch records from DLQ"""
    s3_client = context.resources.s3_resource
    # Fetch records that haven't exceeded max_retries
    pass

@op
def reprocess_record(context, record):
    """Reprocess a single record"""
    try:
        # Attempt to reprocess
        result = process_record(record["raw_data"])
        return {"status": "success", "record_id": record["record_id"]}
    except Exception as e:
        # Increment retry count
        record["retry_count"] += 1
        if record["retry_count"] >= record["max_retries"]:
            record["status"] = "failed_permanently"
        else:
            record["status"] = "pending_reprocessing"
        return {"status": "failed", "record": record}

@op
def update_dlq(context, results):
    """Update DLQ with reprocessing results"""
    # Update records in DLQ
    pass

@job
def reprocess_dlq_job():
    """Job to reprocess DLQ records"""
    records = fetch_dlq_records()
    results = reprocess_record.map(records)
    update_dlq(results)
```

### 2.3 Circuit Breaker Pattern

**Implementation**:
```python
from circuitbreaker import circuit

@circuit(failure_threshold=5, recovery_timeout=60)
def external_api_call():
    """API call with circuit breaker"""
    response = requests.get("https://api.example.com/data")
    response.raise_for_status()
    return response.json()

# Usage
try:
    data = external_api_call()
except CircuitBreakerError:
    # Circuit is open, use fallback
    data = get_cached_data()
except Exception as e:
    # Other errors
    logger.error(f"API call failed: {e}")
    raise
```

**Circuit Breaker States**:
- **Closed**: Normal operation, requests pass through
- **Open**: Failure threshold reached, requests fail immediately
- **Half-Open**: Testing if service has recovered

---

## 3. Data Quality Beyond Schema

### 3.1 Schema Validation

**Pandera Schema Example**:
```python
import pandera as pa
from pandera import Column, Check, DataFrameSchema

station_schema = DataFrameSchema({
    "station_id": Column(str, Check.str_matches(r'^\d+$')),
    "name": Column(str, Check.str_length(min_value=1)),
    "lat": Column(float, Check.between(51.0, 51.2)),
    "lon": Column(float, Check.between(16.8, 17.2)),
    "bikes": Column(int, Check.ge(0)),
    "spaces": Column(int, Check.ge(0)),
    "total_docks": Column(int, Check.ge(0)),
    "timestamp": Column("datetime64[ns]"),
})

# Usage
validated_df = station_schema.validate(df)
```

### 3.2 Business Logic Validation

**Custom Validators**:
```python
class BusinessLogicValidator:
    """Validate business rules"""

    @staticmethod
    def validate_bike_counts(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure bike counts don't exceed total docks"""
        invalid = df[df['bikes'] + df['spaces'] > df['total_docks']]
        if not invalid.empty:
            raise ValueError(
                f"Found {len(invalid)} records where bikes + spaces > total_docks"
            )
        return df

    @staticmethod
    def validate_geographic_bounds(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure coordinates are within expected bounds"""
        bounds = {
            "lat_min": 51.0, "lat_max": 51.2,
            "lon_min": 16.8, "lon_max": 17.2
        }
        out_of_bounds = df[
            (df['lat'] < bounds['lat_min']) |
            (df['lat'] > bounds['lat_max']) |
            (df['lon'] < bounds['lon_min']) |
            (df['lon'] > bounds['lon_max'])
        ]
        if not out_of_bounds.empty:
            raise ValueError(
                f"Found {len(out_of_bounds)} records outside geographic bounds"
            )
        return df

    @staticmethod
    def validate_temporal_consistency(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure timestamps are in chronological order"""
        if not df['timestamp'].is_monotonic_increasing:
            raise ValueError("Timestamps are not in chronological order")
        return df
```

### 3.3 Anomaly Detection

**Statistical Anomaly Detection**:
```python
from sklearn.ensemble import IsolationForest
from scipy import stats

def detect_statistical_anomalies(
    df: pd.DataFrame,
    column: str,
    method: str = "isolation_forest",
    contamination: float = 0.01
) -> pd.DataFrame:
    """Detect anomalies in a column"""

    if method == "isolation_forest":
        model = IsolationForest(contamination=contamination, random_state=42)
        df['anomaly'] = model.fit_predict(df[[column]])
        df['anomaly_score'] = model.score_samples(df[[column]])
        anomalies = df[df['anomaly'] == -1]

    elif method == "zscore":
        z_scores = np.abs(stats.zscore(df[column]))
        df['z_score'] = z_scores
        anomalies = df[z_scores > 3]

    return anomalies

# Usage
anomalies = detect_statistical_anomalies(df, 'bikes', method='isolation_forest')
```

### 3.4 Data Profiling

**Pandas Profiling**:
```python
import pandas_profiling

def profile_data(df: pd.DataFrame, output_path: str):
    """Generate data profile report"""
    profile = pandas_profiling.ProfileReport(df)
    profile.to_file(output_path)

# Usage
profile_data(df, 'data_profile.html')
```

**Custom Profiling**:
```python
def custom_data_profile(df: pd.DataFrame) -> dict:
    """Generate custom data profile"""
    profile = {
        "shape": df.shape,
        "columns": list(df.columns),
        "dtypes": df.dtypes.to_dict(),
        "null_counts": df.isnull().sum().to_dict(),
        "null_percentages": (df.isnull().sum() / len(df) * 100).to_dict(),
        "memory_usage": df.memory_usage(deep=True).to_dict(),
        "numeric_stats": df.describe().to_dict(),
        "categorical_stats": {}
    }

    # Categorical column stats
    for col in df.select_dtypes(include=['object']).columns:
        profile["categorical_stats"][col] = {
            "unique_count": df[col].nunique(),
            "top_values": df[col].value_counts().head(10).to_dict()
        }

    return profile
```

---

## 4. Performance Optimization

### 4.1 Query Optimization

**DuckDB Optimization**:
```python
# Create indexes for frequently queried columns
conn.execute("CREATE INDEX idx_station_id ON wrm_stations(station_id)")
conn.execute("CREATE INDEX idx_timestamp ON wrm_stations(timestamp)")

# Use CTEs for complex queries
query = """
WITH latest_stations AS (
    SELECT
        station_id,
        name,
        bikes,
        spaces,
        ROW_NUMBER() OVER (PARTITION BY station_id ORDER BY timestamp DESC) as rn
    FROM wrm_stations
)
SELECT * FROM latest_stations WHERE rn = 1
"""

# Use materialized views for expensive queries
conn.execute("""
    CREATE MATERIALIZED VIEW mv_station_latest AS
    SELECT * FROM latest_stations WHERE rn = 1
""")
```

### 4.2 Parallel Processing

**Multiprocessing**:
```python
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial

def process_partition(partition_data, config):
    """Process a single partition"""
    # Process data
    return processed_data

@asset
def parallel_processing_asset(context: AssetExecutionContext):
    # Split data into partitions
    partitions = split_data_into_partitions(data, num_partitions=4)

    # Process in parallel
    with ProcessPoolExecutor(max_workers=4) as executor:
        process_func = partial(process_partition, config=context.op_config)
        results = list(executor.map(process_func, partitions))

    # Combine results
    return pd.concat(results)
```

### 4.3 Caching Strategies

**Multi-Level Caching**:
```python
from functools import lru_cache
import redis

# In-memory cache (fast, small)
@lru_cache(maxsize=1000)
def get_station_config(station_id: str):
    """Get station configuration with LRU cache"""
    return fetch_station_config(station_id)

# Redis cache (slower, larger)
redis_client = redis.Redis(host='localhost', port=6379)

def get_cached_data(key: str, fetcher, ttl: int = 3600):
    """Get data with Redis caching"""
    cached = redis_client.get(key)
    if cached:
        return json.loads(cached)

    data = fetcher()
    redis_client.setex(key, ttl, json.dumps(data))
    return data

# Usage
def get_station_data(station_id: str):
    key = f"station:{station_id}"
    return get_cached_data(
        key,
        lambda: fetch_station_data(station_id),
        ttl=300  # 5 minutes
    )
```

### 4.4 Data Sampling

**Adaptive Sampling**:
```python
def adaptive_sample_data(
    data: pd.DataFrame,
    max_points: int = 10000,
    preserve_outliers: bool = True
) -> pd.DataFrame:
    """Sample data while preserving important points"""

    if len(data) <= max_points:
        return data

    # Calculate step size
    step = len(data) // max_points
    sampled = data.iloc[::step].copy()

    if preserve_outliers:
        # Add outliers that might have been missed
        numeric_cols = data.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            mean = data[col].mean()
            std = data[col].std()
            outliers = data[
                (data[col] < mean - 2*std) |
                (data[col] > mean + 2*std)
            ]
            sampled = pd.concat([sampled, outliers])

    return sampled.drop_duplicates()
```

---

## 5. Test Coverage Strategies

### 5.1 Unit Tests

**Asset Unit Test**:
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

def test_wrm_stations_raw_data_success(mock_s3_client):
    """Test successful data fetch"""
    context = build_asset_context(resources={"s3_resource": Mock()})
    result = wrm_stations_raw_data_asset(context)

    assert result is not None
    mock_s3_client.get.assert_called_once()

def test_wrm_stations_raw_data_duplicate_detection(mock_s3_client):
    """Test duplicate detection"""
    context = build_asset_context(resources={"s3_resource": Mock()})

    # First call
    result1 = wrm_stations_raw_data_asset(context)
    # Second call with same data
    result2 = wrm_stations_raw_data_asset(context)

    # Should return same key (duplicate detected)
    assert result1 == result2
```

### 5.2 Integration Tests

**End-to-End Pipeline Test**:
```python
import pytest
from dagster import materialize

@pytest.fixture
def test_resources():
    return {
        "s3_resource": MockS3Client(),
        "duckdb_io_manager": MockDuckDBIOManager()
    }

def test_pipeline_end_to_end(test_resources):
    """Test entire pipeline from raw to enhanced"""
    result = materialize(
        assets=[
            wrm_stations_raw_data_asset,
            wrm_stations_processed_data_all_asset,
            wrm_stations_enhanced_data_all_asset
        ],
        resources=test_resources
    )

    assert result.success
    assert len(result.asset_materializations) == 3
```

### 5.3 Property-Based Testing

**Hypothesis Testing**:
```python
from hypothesis import given, strategies as st
import pandas as pd

@given(st.lists(st.tuples(
    st.integers(min_value=0, max_value=100),
    st.integers(min_value=0, max_value=100),
    st.integers(min_value=100, max_value=200)
)))
def test_bike_count_property(bike_spaces_pairs):
    """Test that bikes + spaces never exceeds total_docks"""
    for bikes, spaces, total_docks in bike_spaces_pairs:
        assert bikes + spaces <= total_docks

@given(st.data())
def test_coordinate_bounds(data):
    """Test that coordinates are within Wrocław bounds"""
    lat = data.draw(st.floats(min_value=51.0, max_value=51.2))
    lon = data.draw(st.floats(min_value=16.8, max_value=17.2))

    assert 51.0 <= lat <= 51.2
    assert 16.8 <= lon <= 17.2
```

### 5.4 Test Coverage Targets

| Component | Target Coverage |
|-----------|----------------|
| Assets | 80%+ |
| Jobs | 80%+ |
| Sensors | 80%+ |
| Resources | 70%+ |
| Utilities | 90%+ |

---

## 6. CI/CD for Data Pipelines

### 6.1 GitHub Actions Workflow

```yaml
# .github/workflows/dagster-ci.yml
name: Dagster CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install ruff black mypy
      - name: Run linters
        run: |
          ruff check wrm_pipeline/
          black --check wrm_pipeline/
          mypy wrm_pipeline/

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
      - name: Run tests
        run: |
          pytest wrm_pipeline/wrm_pipeline_tests/ \
            --cov=wrm_pipeline \
            --cov-report=xml \
            --cov-report=html
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  deploy:
    needs: [lint, test]
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

### 6.2 Deployment Strategies

**Blue-Green Deployment**:
```bash
# Deploy to green environment
git clone https://github.com/repo/bike-data-flow.git bike-data-flow-green
cd bike-data-flow-green
git checkout main
pip install -e .
systemctl start dagster-daemon-green

# Switch traffic
systemctl stop dagster-daemon-blue
systemctl start dagster-daemon-green

# Rollback if needed
systemctl stop dagster-daemon-green
systemctl start dagster-daemon-blue
```

**Canary Deployment**:
```python
# Gradually route traffic to new version
def canary_deployment(new_version_ratio: float = 0.1):
    """Route percentage of traffic to new version"""
    if random.random() < new_version_ratio:
        return process_with_new_version()
    else:
        return process_with_old_version()
```

---

## Related Memory Files

- [`pipeline-analysis.md`](pipeline-analysis.md) - Current pipeline architecture
- [`improvement-roadmap.md`](improvement-roadmap.md) - Prioritized improvements
- [`infrastructure-constraints.md`](infrastructure-constraints.md) - Hetzner S3/VPS constraints

---

*Last Updated: 2026-01-09*
*Research Phase: Complete*
