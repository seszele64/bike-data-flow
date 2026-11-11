# Bike Data Flow Pipeline

A comprehensive data orchestration pipeline built with Dagster for processing and analyzing bike-sharing station data from Wrocław's WRM (Wrocławski Rower Miejski) system, paired with an Evidence dashboard for interactive reporting.

## 🎯 Goals

- **Real-time Data Ingestion**: Automatically fetch bike station data from WRM API
- **Automated Data Processing**: Event-driven processing pipeline triggered by new data arrivals
- **Data Processing & Transformation**: Clean, process, and enhance raw station data
- **Data Storage & Analytics**: Store processed data in S3 and provide DuckDB analytics views
- **Data Quality & Monitoring**: Provide comprehensive summaries and processing statistics
- **Scalable Architecture**: Handle large volumes of historical and real-time bike station data
- **Interactive Reporting**: Publish an Evidence dashboard with Leaflet maps to GitHub Pages

## 🛠️ Tech Stack

### Core Technologies
- **Dagster**: Data orchestration and pipeline management
- **S3-Compatible Storage**: Object storage for raw, processed, and enhanced data files
- **DuckDB**: High-performance analytical processing and querying
- **Evidence**: Static site generator for interactive data dashboards and reports
- **Pandas**: Data manipulation and transformation

### Dashboard & Publishing
- **Leaflet**: Interactive maps embedded in the dashboard
- **OpenStreetMap**: Base map tiles (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright))
- **GitHub Pages**: Hosting for the published dashboard

### Supporting Libraries
- **requests**: HTTP API calls for data ingestion
- **ftfy**: Text encoding fixes
- **pandera**: Data validation and schema enforcement
- **pydantic**: Data modeling and validation

## 📊 Data Flow Architecture

### Pipeline Overview
```
WRM API → Raw Data (S3) → [Sensor Detection] → Processing → Enhancement → Analytics (DuckDB)
```

### Automated Processing Flow
1. **Data Ingestion**: Raw data asset fetches data from WRM API and stores in S3
2. **Event Detection**: Sensor monitors S3 for new raw data files
3. **Automated Processing**: Sensor triggers processing job when new data arrives
4. **Data Enhancement**: Processing job runs both processing and enhancement assets
5. **Analytics Ready**: Enhanced data available for DuckDB analysis

### Asset Groups

#### 1. **Data Acquisition** (`wrm_data_acquisition`)
- [`wrm_stations_raw_data`](wrm_pipeline/wrm_pipeline/assets/stations/raw_all.py): Downloads fresh data from WRM API with deduplication

#### 2. **Data Processing** (`wrm_data_processing`) 
- [`wrm_stations_processed_data_all`](wrm_pipeline/wrm_pipeline/assets/stations/processed_all.py): Processes raw data and converts to Parquet with validation

#### 3. **Enhanced Data** (`enhanced_data`)
- [`wrm_stations_enhanced_data_all`](wrm_pipeline/wrm_pipeline/assets/stations/enhanced_all.py): Creates enhanced datasets with metadata enrichment

#### 4. **Analytics Views** (`analytics_views`)
- [`duckdb_enhanced_views`](wrm_pipeline/wrm_pipeline/assets/duckdb/create_enhanced_views.py): Creates DuckDB views for enhanced data analysis
- [`station_summary`](wrm_pipeline/wrm_pipeline/assets/duckdb/query_station_summary.py): Generates station summary statistics
- [`bike_density_spatial_analysis`](wrm_pipeline/wrm_pipeline/assets/duckdb/bike_spatial_density_analysis.py): Spatial density analysis for bike distribution
- [`bike_density_map`](wrm_pipeline/wrm_pipeline/assets/duckdb/bike_spatial_density_analysis.py): Interactive density mapping

#### 5. **Dashboard** (`dashboard`)
- [`evidence_data_snapshot`](wrm_pipeline/wrm_pipeline/assets/dashboard.py): Exports the dashboard datasets (stations_latest, density_grid) to the local DuckDB snapshot and S3 Parquet
- [`evidence_build`](wrm_pipeline/wrm_pipeline/assets/dashboard.py): Runs the Evidence static build (`npm run build`) over the snapshot
- [`evidence_deploy`](wrm_pipeline/wrm_pipeline/assets/dashboard.py): Validates the credential-free static build output for GitHub Pages deployment

## 🔄 Automation & Event-Driven Processing

### **Sensor-Based Automation**
The pipeline features an intelligent sensor system that automatically detects new data and triggers processing:

#### **WRM Stations Raw Data Sensor** ([`wrm_stations_raw_data_sensor`](wrm_pipeline/wrm_pipeline/sensors/stations.py))
- **Monitoring**: Continuously monitors S3 for new raw data files every 30 seconds
- **Detection Logic**: Tracks file timestamps using cursor-based state management
- **Partition Awareness**: Groups files by date partition for efficient processing
- **Job Triggering**: Automatically launches processing job when new data arrives

#### **Processing Job** ([`wrm_stations_processing_job`](wrm_pipeline/wrm_pipeline/jobs/stations.py))
- **Asset Selection**: Runs both processing and enhancement assets in sequence
- **Dependency Management**: Respects asset dependencies for correct execution order
- **Partition Processing**: Handles date-partitioned data processing automatically

### **Event Flow**
```
1. Raw Data Arrives → S3 storage (via raw data asset)
2. Sensor Detection → Monitors S3 every 30 seconds
3. New File Found → Sensor creates RunRequest for date partition
4. Job Execution → Processes raw data → Creates enhanced data
5. Analytics Ready → Data available in DuckDB views
```

### **Key Automation Features**
- **Zero Manual Intervention**: Once enabled, pipeline runs fully automatically
- **Duplicate Handling**: Sensor only processes genuinely new files
- **Partition Intelligence**: Automatically handles different date partitions
- **Error Resilience**: Continues monitoring even if individual runs fail
- **State Management**: Maintains processing history via cursor mechanism

### **Scheduled Refresh**

Two Dagster schedules complement the sensor path
([`definitions.py`](wrm_pipeline/wrm_pipeline/definitions.py)):

- **`latest_refresh`** (`*/15 * * * *`, UTC) — runs the unpartitioned
  raw fetch → latest processed → latest enhanced → DuckDB views → dashboard
  snapshot chain, rewriting the published `wrm/stations_latest.parquet` and
  `wrm/density_grid.parquet` every 15 minutes.
- **`daily`** (`0 5 * * *`, UTC) — runs the partitioned archive job for
  yesterday's partition; it does not write the two published dashboard Parquet
  objects.

The Evidence publish workflow polls seven minutes after each `latest_refresh`
tick; see the [Dashboard Publish Pipeline](#dashboard-publish-pipeline-pagesyml).

## 🔄 Data Processing Flow

### 1. **Data Ingestion**
```python
WRM API → S3 Raw Storage (Partitioned by date)
- Format: Text files with CSV data
- Partition: dt=YYYY-MM-DD/
- Encoding: UTF-8 with automatic fixing
- Deduplication: Hash-based duplicate detection
```

### 2. **Event Detection & Triggering**
```python
S3 File Monitoring → Sensor Detection → Job Triggering
- Monitoring Interval: 30 seconds
- State Tracking: Cursor-based timestamp tracking
- Partition Grouping: Date-based partition processing
- Run Request Creation: Automatic job execution
```

### 3. **Data Processing**
```python
Raw Data → Processed Data (Parquet)
- Column splitting and type conversion
- Timestamp normalization
- Data validation with Pandera schemas
- Parquet optimization for analytics
```

### 4. **Data Enhancement**
```python
Processed Files → Enhanced Data
- Metadata enrichment (file timestamps, processing dates)
- Record type classification (station vs bike data)
- Daily partitioning for efficient access
```

### 5. **Analytics & Views**
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
├── raw/dt=YYYY-MM-DD/           # Raw API data files (monitored by sensor)
├── processed/all/dt=YYYY-MM-DD/ # Processed Parquet files
└── enhanced/all/dt=YYYY-MM-DD/  # Enhanced datasets with metadata
```

### DuckDB Analytics Database
- **Location**: `db/analytics.duckdb` (repo-root relative; override with `WRM_DUCKDB_PATH`)
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

## 📊 Evidence Dashboard

The pipeline publishes an [Evidence](https://evidence.dev) static dashboard that renders the
`stations_latest` and `density_grid` snapshots as tables and interactive Leaflet maps. Both
[`PointMap`](dashboard/pages/index.md) (stations) and [`BubbleMap`](dashboard/pages/index.md)
(density) overlay the data on OpenStreetMap base tiles.

- **Base map tiles**: `https://tile.openstreetmap.org/{z}/{x}/{y}.png` — the OpenStreetMap tile
  URL template. Because it contains `{z}`, `{x}` and `{y}` braces, the template is passed to the
  maps as a braces-wrapped JS string (`basemap={"https://tile.openstreetmap.org/{z}/{x}/{y}.png"}`)
  so mdsvex does not parse `{z}/{x}/{y}` as Svelte expressions.
- **Attribution (required by OSM's tile policy)**: © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
- **Deployed dashboard**: <https://seszele64.github.io/bike-data-flow/> (GitHub Pages, built with basePath `/bike-data-flow`).

### Dashboard Publish Pipeline ([`pages.yml`](.github/workflows/pages.yml))

The dashboard is rebuilt and redeployed by
[`.github/workflows/pages.yml`](.github/workflows/pages.yml). A `build` job
assembles the DuckDB snapshot from S3 Parquet, runs the Evidence static build
(`@evidence-dev/evidence` 40.1.8), and uploads the
`dashboard/build/bike-data-flow` artifact; a `deploy` job then publishes it to
GitHub Pages, and a post-deploy `record-freshness` job writes the Parquet
fingerprint that the next scheduled run compares against.

#### Trigger matrix

| Trigger | Event name | Publishes? | Notes |
|---------|-----------|------------|-------|
| Push to `main` | `push` | Always | Any merge to `main` redeploys. |
| Manual run | `workflow_dispatch` | Always | Run from the Actions tab. |
| Parquet refresh | `repository_dispatch` (`wrm-parquet-updated`) | Always | Event-driven rebuild (see contract below). |
| 15-minute schedule | `schedule` (`7,22,37,52 * * * *`) | Only when data changed | Polls seven minutes after each `latest_refresh` tick; skipped when the Parquet fingerprint matches the marker written by the last successful deploy. |

The scheduled poll fires at minutes **07, 22, 37 and 52 of every hour (UTC)** —
seven minutes after each `latest_refresh` tick (`*/15 * * * *`,
[`definitions.py`](wrm_pipeline/wrm_pipeline/definitions.py)), which runs the
raw fetch → latest processed → latest enhanced → DuckDB views →
`evidence_data_snapshot` chain that rewrites `wrm/stations_latest.parquet` and
`wrm/density_grid.parquet`. The seven-minute offset lets the S3 writes settle,
but it is **best-effort**: a slow refresh can still overlap a poll, in which
case the next poll picks up the changed Parquet. The cron schedule is a
fallback, not the primary path — `repository_dispatch` (below) is the
event-driven trigger and publishes as soon as O2 announces fresh Parquet.

The separate daily **archive** schedule (`daily`, `0 5 * * *`, UTC) materializes
the day-partitioned history (`processed_all` / `enhanced_all`); it does **not**
write the two published dashboard Parquet objects, so it is unrelated to the
Pages poll cadence.

Because the marker is only written after a successful deploy, a scheduled run
whose build or deploy failed (or was skipped) leaves the marker untouched and
retries the publish on the next poll. Unchanged Parquet with a recorded marker
skips the artifact-producing `Assemble` → `Fetch sources` → `Clean` → `Build` →
`Upload` steps, the deploy **and** the marker write. A poll with unchanged
Parquet therefore does no build/upload or deploy work, but the checkout, Node.js
setup, `npm ci` and freshness probe still run before the skip is decided.

#### Freshness gate (skip when unchanged)

Before any build work, the ungated `Check S3 parquet freshness` step probes the
two published objects (`wrm/stations_latest.parquet`, `wrm/density_grid.parquet`)
and computes a SHA-256 fingerprint of their `ETag` + `LastModified` metadata.
It compares that fingerprint against the marker object
`wrm/.pages-freshness-fingerprint`, which is written by the post-deploy
`record-freshness` job after the last **successful** publish, and emits
`stale=true|false` on `$GITHUB_OUTPUT`.

- **Read-only probe**: the build step only `HEAD`s the two Parquet objects and
  `GET`s the marker. It never writes the marker, so a probe can never mark data
  fresh before it has actually been published.
- **Fail-open**: any probe error (missing AWS CLI, failed `HEAD`, empty
  metadata, missing marker) yields `stale=true` and the step still exits `0`,
  so a broken check never blocks a publish.
- **Publish gate**: the `Assemble` → `Fetch sources` → `Clean` → `Build` →
  `Upload` steps run only when `github.event_name != 'schedule' || steps.freshness.outputs.stale == 'true'`.
  Push, manual and dispatch runs therefore always publish; a **scheduled run
  with unchanged Parquet produces no artifact and skips the build/upload and
  deploy work**, though the job setup (checkout, Node.js, `npm ci`) and the
  freshness probe still run.
- **Deploy gate**: the `deploy` job requires `github.ref == 'refs/heads/main'`
  and, on scheduled runs, `needs.build.outputs.stale == 'true'` — so the
  unchanged-schedule skip is shared with `deploy` without re-probing S3.

#### Post-deploy marker recording and retry

The `record-freshness` job runs only after both `build` and `deploy` finish
successfully:

```yaml
if: ${{ !cancelled() && needs.build.result == 'success' && needs.deploy.result == 'success' }}
```

- **Fresh recomputation**: rather than reusing the build-time `stale` value, the
  job recomputes the same fingerprint (SHA-256 over both objects' `ETag` +
  `LastModified`, in `stations_latest.parquet` → `density_grid.parquet` order).
  Probe and recorder must stay byte-identical; the tests pin that contract.
- **Post-deploy equality check**: the recomputed fingerprint is written to
  `s3://$BUCKET_NAME/wrm/.pages-freshness-fingerprint` with `s3api put-object`
  **only if it still equals** the fingerprint the build actually assembled from
  (`needs.build.outputs.fingerprint`, passed into the step as `BUILD_FP`). A
  missing value (the build probe failed open) and a changed value are warned
  with distinct `::warning::` messages; both skip the write and exit `0`, so
  the previous marker stays in place and the next scheduled run re-publishes.
- **Residual timing caveat**: the equality check is a point-in-time comparison,
  not proof that the deployed bytes are identical to what S3 holds now. If a
  Parquet object moves from `A` to `B` and back to `A` between the build's
  probe/assemble and the recorder's re-probe, both observations are `A`, so the
  marker records `A` even though the deployed site may have assembled `B` (or a
  mix of versions). The next scheduled run then treats `A` as already
  published, leaving that copy stale until the Parquet changes again. The
  window is normally one build-and-deploy cycle and is not detected, so the
  gate is **at-least-once** rather than a byte-for-byte publication guarantee.
  If data can flap across a deploy, trigger a `repository_dispatch` or re-run
  after it settles.
- **Best-effort**: the step deliberately omits `set -e`. A missing AWS CLI, an
  unreachable bucket, a failed `HEAD`, a missing build fingerprint, changed
  Parquet, or a rejected `PutObject` each emits a `::warning::` and exits `0`,
  so marker bookkeeping can never fail the publish.
- **Retry semantics**: the marker advances only when the whole
  `build` → `deploy` → `record-freshness` chain succeeds **and** the post-deploy
  re-probe still matches the build fingerprint. A failed, skipped or cancelled
  deploy means the job never runs; a failed marker write leaves the previous
  fingerprint in place. Either way the next scheduled run still sees changed
  Parquet (`stale=true`) and publishes again. The gate is therefore
  **at-least-once**: it may republish the same data, but it never records data
  as fresh unless a deploy has succeeded and the fingerprint still matches at
  record time.
- **Permission caveat**: the credentials behind `S3_ACCESS_KEY_ID` need
  `s3:PutObject` on the marker key, while the probe needs only
  `s3:GetObject`/`HeadObject`. That write permission has **not** been verified
  against the production credential. If it is denied, the deploy still
  succeeds, the job warns, the marker never advances, and every scheduled run
  republishes — the warning is the signal to grant `PutObject` on
  `wrm/.pages-freshness-fingerprint`.

#### O2 dispatch contract (`repository_dispatch`)

When O2's Parquet publisher lands fresh `stations_latest` / `density_grid`
Parquet in S3 it may rebuild the dashboard immediately instead of waiting for
the next scheduled poll (at most ~15 minutes away) by sending a repository
dispatch to this repository:

```jsonc
// POST /repos/seszele64/bike-data-flow/dispatches
// Authorization: Bearer <sender PAT or GitHub App installation token>
// Accept: application/vnd.github+json
{
  "event_type": "wrm-parquet-updated",
  "client_payload": {}          // optional; unused by the workflow today
}
```

- **`event_type`** must be exactly `wrm-parquet-updated` (the `repository_dispatch.types`
  entry); other event types do not trigger a run.
- **Authentication**: the sender uses **its own** PAT/App token. No token is
  embedded in the workflow — `pages.yml` declares no dispatch credentials and
  receives no secret from the caller.
- **Effect**: a dispatch is treated like a push (publish always) and bypasses
  the freshness comparison, so the build runs even if the fingerprint is
  unchanged.

#### Credential scoping

S3 credentials (`S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`,
`BUCKET_NAME`) are injected **by name only** via `${{ secrets.* }}` — four
names, in four scopes (16 secret references in the file):

- three step-level `env` blocks in `build` for the steps that read the private
  bucket: `Check S3 parquet freshness`, `Assemble wrm.duckdb from S3 parquet`,
  and `Fetch sources`;
- one job-level `env` block on `record-freshness`, inherited by its single
  marker-writing step; that step declares only the non-secret `BUILD_FP`
  (`${{ needs.build.outputs.fingerprint }}`) in a step-level `env` block, so it
  still receives no credential of its own.

No secret value — credential, endpoint, ETag, fingerprint or bucket name — is
ever echoed to the logs. `npm run build`, the artifact upload and the whole
`deploy` job stay secret-free, so the `github-pages` environment and the
deploy action never receive bucket credentials; only the post-deploy job
attempts a write, and only to the marker key.

> **Verification**: the trigger matrix, gate strings, read-only probe,
> post-deploy marker job, retry gate and credential scoping above are pinned by
> [`test_pages_workflow.py`](wrm_pipeline/tests/unit/dashboard/test_pages_workflow.py)
> and [`test_evidence_deploy.py`](wrm_pipeline/tests/unit/dashboard/test_evidence_deploy.py)
> (offline unit tests, no network or secrets):
>
> ```bash
> uv run pytest \
>   wrm_pipeline/tests/unit/dashboard/test_pages_workflow.py \
>   wrm_pipeline/tests/unit/dashboard/test_evidence_deploy.py
> ```

## 📁 Project Structure

```
bike-data-flow/
├── dashboard/                          # Evidence static dashboard (GitHub Pages)
│   ├── evidence.config.yaml            # Evidence project configuration
│   ├── package.json                    # Node dependencies & build/deploy scripts
│   ├── pages/
│   │   └── index.md                    # PointMap (stations) & BubbleMap (density) pages
│   └── sources/
│       └── wrm/                        # DuckDB source feeding the dashboard
│           ├── connection.yaml
│           ├── stations_latest.sql      # Stations snapshot query
│           └── density_grid.sql         # Spatial density grid query
├── db/
│   └── analytics.duckdb                # Generated analytics database (git-ignored)
├── docs/                               # Vault operations & deployment documentation
├── specs/                              # Feature specifications (vault integration, retry)
├── .github/
│   └── workflows/
│       ├── gitleaks.yml                # Secret scanning
│       └── pages.yml                   # Evidence build & Pages deploy (freshness-gated)
├── config/
│   └── vault/                          # Vault server config, policies & audit devices
├── scripts/
│   └── vault/                          # Vault init, backup, restore & health-check scripts
├── storage/                            # Object-storage client & partition helpers
├── wrm_pipeline/
│   ├── wrm_pipeline/                   # Installable pipeline package
│   │   ├── assets/
│   │   │   ├── stations/               # Raw ingestion, processing & enhancement assets
│   │   │   ├── duckdb/                 # DuckDB view creation & analysis scripts
│   │   │   └── dashboard.py            # Dashboard build & data-snapshot assets
│   │   ├── sensors/                    # Event detection sensors
│   │   ├── jobs/                       # Processing job definitions
│   │   ├── models/                     # Data models and schemas
│   │   ├── vault/                      # Vault client, rotation & policy helpers
│   │   ├── retry/                      # API/S3 retry & circuit-breaker logic
│   │   ├── migration/                  # Secret migration to Vault
│   │   ├── ops/                        # Operational helpers
│   │   ├── config.py                   # Configuration management
│   │   └── definitions.py              # Dagster definitions entrypoint
│   ├── tests/
│   │   ├── unit/                       # Unit tests (default pytest target)
│   │   ├── integration/                # Integration tests (run with -m integration)
│   │   └── BASELINE.md                 # Recorded pre-ETL pytest baseline
│   └── pyproject.toml                  # Package metadata & dependency groups
├── pyproject.toml                      # uv workspace root & pytest configuration
├── conftest.py                         # Shared pytest fixtures & marker bootstrap
├── uv.lock                             # Locked workspace dependency versions
└── README.md                           # Project documentation
```

## 🚀 Key Features

### **Event-Driven Architecture**
- Sensor-based automatic processing triggered by new data arrivals
- No manual intervention required once pipeline is enabled
- Intelligent duplicate detection and state management
- Partition-aware processing for efficient data handling

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
- Sensor monitoring intervals

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

# Analytics DB lives at the repo root: <repo>/db/analytics.duckdb
# (override with the WRM_DUCKDB_PATH environment variable)
db_path = os.environ.get("WRM_DUCKDB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "db", "analytics.duckdb"
)

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

Requires [uv](https://docs.astral.sh/uv/) — Python 3.12 is pinned via `.python-version` and provisioned automatically.

```bash
# Install dependencies (uv workspace; resolved from uv.lock; --all-packages
# includes wrm_pipeline's dev group: pytest, pytest-cov, dagster-webserver)
uv sync --frozen --all-packages

# Launch Dagster UI
uv run dagster-webserver -f wrm_pipeline
```

### 3. **Enable Automation**
- **Enable Sensor**: In Dagster UI, navigate to Automation → Sensors
- **Turn On Sensor**: Enable `wrm_stations_raw_data_sensor`
- **Monitor Activity**: Sensor will check for new data every 30 seconds

### 4. **Initialize Data Processing**
- Run initial data ingestion from WRM API (manually or via schedule)
- Sensor will automatically detect and process new raw data
- Enhanced data will be available for analytics

### 5. **Access Analytics**
- Use DuckDB CLI or Python for data analysis
- Query enhanced views for insights
- Build dashboards using latest station views

## 🧪 Testing

Tests live in [`wrm_pipeline/tests/`](wrm_pipeline/tests/) (`unit/` and `integration/`), with pytest configured in the root [`pyproject.toml`](pyproject.toml) under `[tool.pytest.ini_options]`. The default run collects the **unit suite only** (`testpaths = ["wrm_pipeline/tests/unit"]`).

```bash
# Install/sync the environment first (--all-packages includes the dev group)
uv sync --frozen --all-packages

# Unit tests (recorded baseline: 206 passed, 3 skipped, 1 xfailed, 15 warnings — see wrm_pipeline/tests/BASELINE.md)
uv run pytest

# Integration tests — explicitly selected; currently a placeholder suite
uv run pytest wrm_pipeline/tests/integration -m integration

# Filter by keyword
uv run pytest -k vault
```

Registered markers: `integration` (touches external systems — S3, Vault, live APIs), `evidence` (asserts against recorded baseline/evidence artifacts), `slow` (excluded from the fast feedback loop). Tests collected from `tests/integration/` are auto-marked `integration` by the root `conftest.py`.

## 🔍 Monitoring & Operations

### **Sensor Monitoring**
- **Status**: Check sensor status in Dagster UI (Automation tab)
- **Logs**: View sensor evaluation logs for debugging
- **Cursor State**: Monitor processing state via cursor tracking
- **Run History**: Track automatically triggered job runs

### **Troubleshooting**
- **Sensor Not Triggering**: Check S3 credentials and bucket access
- **Processing Failures**: Review job logs for specific error details
- **Duplicate Processing**: Sensor's cursor mechanism prevents duplicates
- **Performance**: Adjust monitoring interval if needed (currently 30 seconds)

This pipeline provides a complete solution for automated bike-sharing data processing, from raw API ingestion to advanced analytics, with modern data engineering practices including event-driven automation, schema validation, partitioning, and high-performance analytical capabilities.
