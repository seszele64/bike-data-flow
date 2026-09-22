"""Dashboard evidence snapshot: materialize analytics views into a static DuckDB file.

The dashboard (``dashboard/``) reads ``dashboard/sources/wrm/wrm.duckdb``.
Writing real tables there keeps the frontend free of S3/httpfs dependencies:
the snapshot is a plain, credential-free DuckDB file holding the
``stations_latest`` and ``density_grid`` tables.
"""

import os
import subprocess

import duckdb
from dagster import AssetExecutionContext, MaterializeResult, asset

# dashboard.py sits one directory below the package root (unlike the
# duckdb/ assets, which are two deep), so config is two dots up: this
# resolves both as wrm_pipeline.wrm_pipeline.config (tests) and
# wrm_pipeline.config (dagster CLI code-location loading).
from ..config import (
    HETZNER_ACCESS_KEY_ID,
    HETZNER_ENDPOINT,
    HETZNER_ENDPOINT_URL,
    HETZNER_SECRET_ACCESS_KEY,
    db_path,
)
from .duckdb.create_enhanced_views import create_duckdb_enhanced_views

# Repo root: two levels up from the wrm_pipeline/wrm_pipeline package dir
# (same derivation as config.db_path).
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')
)
SNAPSHOT_PATH = os.path.join(_REPO_ROOT, 'dashboard', 'sources', 'wrm', 'wrm.duckdb')

# Latest station records, projected to the dashboard's expected schema.
_STATIONS_SNAPSHOT_SQL = """
CREATE OR REPLACE TABLE wrm_dash.stations_latest AS
SELECT station_id, name, bikes, spaces, total_docks, installed, lat, lon, timestamp
FROM wrm_stations_latest;
"""

# 1000m^2 grid cells: SQL port of bike_spatial_density_analysis._analyze_grid_density
# (~31.6m cell side, density_per_1000m2 == bikes per cell).
_DENSITY_SNAPSHOT_SQL = """
CREATE OR REPLACE TABLE wrm_dash.density_grid AS
WITH bounds AS (
    SELECT MIN(lat) AS min_lat, MAX(lat) AS max_lat, MIN(lon) AS min_lon, MAX(lon) AS max_lon
    FROM wrm_stations_latest WHERE lat IS NOT NULL AND lon IS NOT NULL
),
grid AS (
    SELECT min_lat, min_lon,
           sqrt(1000) / 111320.0 AS lat_delta,
           sqrt(1000) / (111320.0 * cos(radians((min_lat + max_lat) / 2))) AS lon_delta
    FROM bounds
),
cells AS (
    SELECT floor((s.lat - g.min_lat) / g.lat_delta) AS i_lat,
           floor((s.lon - g.min_lon) / g.lon_delta) AS i_lon,
           s.bikes
    FROM wrm_stations_latest s, grid g
    WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
)
SELECT g.min_lat + (c.i_lat + 0.5) * g.lat_delta AS grid_lat,
       g.min_lon + (c.i_lon + 0.5) * g.lon_delta AS grid_lon,
       CAST(SUM(c.bikes) AS BIGINT) AS bike_count,
       CAST(COUNT(*) AS BIGINT) AS station_count,
       CAST(SUM(c.bikes) AS BIGINT) AS density_per_1000m2
FROM cells c, grid g
GROUP BY g.min_lat, g.min_lon, g.lat_delta, g.lon_delta, c.i_lat, c.i_lon;
"""


@asset(
    name="evidence_data_snapshot",
    compute_kind="duckdb",
    group_name="dashboard",
    deps=[create_duckdb_enhanced_views],
)
def evidence_data_snapshot(context: AssetExecutionContext) -> MaterializeResult:
    """Snapshot the analytics views into the dashboard's static DuckDB file."""
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    with duckdb.connect(db_path) as conn:
        # S3 SET pattern from query_station_summary.py so the S3-backed views
        # can be read; the endpoint must be a bare hostname for DuckDB.
        conn.execute("INSTALL httpfs;")
        conn.execute("LOAD httpfs;")
        conn.execute("SET s3_region='auto';")
        conn.execute(f"SET s3_access_key_id='{HETZNER_ACCESS_KEY_ID}';")
        conn.execute(f"SET s3_secret_access_key='{HETZNER_SECRET_ACCESS_KEY}';")
        endpoint = HETZNER_ENDPOINT_URL or HETZNER_ENDPOINT or ""
        if endpoint.startswith(('http://', 'https://')):
            endpoint = endpoint.replace('https://', '').replace('http://', '')
        conn.execute(f"SET s3_endpoint='{endpoint}';")
        conn.execute("SET s3_use_ssl='true';")
        conn.execute("SET s3_url_style='path';")

        conn.execute(f"ATTACH '{SNAPSHOT_PATH}' AS wrm_dash;")
        try:
            conn.execute(_STATIONS_SNAPSHOT_SQL)
            conn.execute(_DENSITY_SNAPSHOT_SQL)
            stations = conn.execute("SELECT COUNT(*) FROM wrm_dash.stations_latest;").fetchone()[0]
            cells = conn.execute("SELECT COUNT(*) FROM wrm_dash.density_grid;").fetchone()[0]
        finally:
            conn.execute("DETACH wrm_dash;")

    context.log.info(
        f"Dashboard snapshot written to {SNAPSHOT_PATH} "
        f"({stations} stations, {cells} grid cells)"
    )
    return MaterializeResult(
        metadata={
            "snapshot_path": SNAPSHOT_PATH,
            "stations_latest_rows": stations,
            "density_grid_rows": cells,
        }
    )


# --- evidence_build (B4.4) -------------------------------------------------
# Dashboard project dir and static build output, both env-overridable so a
# deployment can relocate the checkout; defaults derive from the repo root
# (same derivation as SNAPSHOT_PATH above).
DASHBOARD_DIR = os.environ.get('WRM_DASHBOARD_DIR') or os.path.join(_REPO_ROOT, 'dashboard')
# `evidence build` exports the static site into <dashboard>/build by default.
BUILD_DIR = os.environ.get('WRM_DASHBOARD_BUILD_DIR') or os.path.join(DASHBOARD_DIR, 'build')

# Secret env prefixes stripped from the npm child environment: the static
# build must be credential-free, so HETZNER_*/S3_* secrets are never handed
# to Node (and, as below, never logged).
_SECRET_ENV_PREFIXES = ('HETZNER_', 'S3_')


def _build_env() -> dict:
    """Environment for the npm child process, minus S3/Hetzner secrets.

    Reads os.environ only (no hardcoded keys); values are filtered out, never
    printed.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(_SECRET_ENV_PREFIXES)
    }


@asset(
    name="evidence_build",
    compute_kind="npm",
    group_name="dashboard",
    deps=[evidence_data_snapshot],
)
def evidence_build(context: AssetExecutionContext) -> MaterializeResult:
    """Run ``npm run build`` (Evidence static export) over the snapshot.

    Depends on ``evidence_data_snapshot`` so the credential-free DuckDB file
    exists before the site is built; no S3 credentials reach the build.
    """
    if not os.path.isfile(os.path.join(DASHBOARD_DIR, 'package.json')):
        raise FileNotFoundError(
            f"dashboard package.json not found under {DASHBOARD_DIR} "
            "(set WRM_DASHBOARD_DIR to relocate it)"
        )
    if not os.path.isdir(os.path.join(DASHBOARD_DIR, 'node_modules')):
        raise FileNotFoundError(
            f"node_modules missing in {DASHBOARD_DIR}; run `npm ci` there first"
        )

    try:
        proc = subprocess.run(
            ['npm', 'run', 'build'],
            cwd=DASHBOARD_DIR,
            env=_build_env(),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("npm not found on PATH") from exc

    # Log command output tails only; the environment (and any secrets in it)
    # is never logged.
    if proc.stdout:
        context.log.info(proc.stdout[-2000:])
    if proc.returncode != 0:
        context.log.error(proc.stderr[-2000:])
        raise RuntimeError(f"`npm run build` failed with exit code {proc.returncode}")

    return MaterializeResult(
        metadata={
            "dashboard_dir": DASHBOARD_DIR,
            "build_dir": BUILD_DIR,
            "build_exists": os.path.isdir(BUILD_DIR),
            "command": "npm run build",
        }
    )


# --- evidence_deploy (B5.3) ------------------------------------------------
# Static-hosting handoff: no upload happens here (the GitHub Pages workflow
# owns the publish step), so this asset only validates the credential-free
# build output produced by ``evidence_build``.


@asset(
    name="evidence_deploy",
    compute_kind="static",
    group_name="dashboard",
    deps=[evidence_build],
)
def evidence_deploy(context: AssetExecutionContext) -> MaterializeResult:
    """Verify the static build output is ready for static hosting.

    Depends on ``evidence_build``; checks that ``BUILD_DIR`` exists and
    counts its files so the materialization records what would be published.
    No S3 upload occurs here (the Pages workflow handles upload), and the
    environment is filtered through ``_build_env`` so secrets never reach
    any child process or the logs.
    """
    if not os.path.isdir(BUILD_DIR):
        raise FileNotFoundError(
            f"static build output not found at {BUILD_DIR} "
            "(set WRM_DASHBOARD_BUILD_DIR to relocate it)"
        )

    file_count = sum(
        len(filenames)
        for _, _, filenames in os.walk(BUILD_DIR)
    )
    if file_count == 0:
        raise RuntimeError(f"static build output at {BUILD_DIR} is empty")

    # Ensure the deploy context inherits the same secret-free environment
    # contract as the build (currently upload-free, but kept consistent so a
    # future uploader cannot accidentally receive HETZNER_*/S3_* credentials).
    env = _build_env()
    assert all(not k.upper().startswith(_SECRET_ENV_PREFIXES) for k in env)

    context.log.info(
        f"Static build ready at {BUILD_DIR} ({file_count} files); "
        "upload handled by the Pages workflow"
    )
    return MaterializeResult(
        metadata={
            "build_dir": BUILD_DIR,
            "file_count": file_count,
            "upload": "pages-workflow",
        }
    )
