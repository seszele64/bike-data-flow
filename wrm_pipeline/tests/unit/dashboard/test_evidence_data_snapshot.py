"""Unit tests for the dashboard ``evidence_data_snapshot`` asset (S3 export).

Contract under test
-------------------
``wrm_pipeline/assets/dashboard.py`` (S3-02) materializes two analytics
datasets as session-scoped TEMP tables and publishes them as parquet objects
via DuckDB httpfs ``COPY ... TO``:

- ``wrm/stations_latest.parquet`` — projection of the ``wrm_stations_latest``
  enhanced view onto the dashboard schema (station_id, name, bikes, spaces,
  total_docks, installed, lat, lon, timestamp).
- ``wrm/density_grid.parquet`` — 1000 m² grid aggregation (SQL port of
  ``bike_spatial_density_analysis._analyze_grid_density``): bikes summed per
  ~31.6 m cell, cell centers emitted as grid_lat/grid_lon.

The write is a dual-write: the two datasets land in the local
``dashboard/sources/wrm/wrm.duckdb`` (``ATTACH ... AS snapshot_db`` into a
sibling ``*.tmp``, then an atomic ``os.replace``) AND the two S3 parquet
objects. ``SNAPSHOT_PATH`` is redirected to ``tmp_path``, where the local
file must exist with both tables, and no leftover ``*.tmp`` may remain.

After both parquet COPYs the asset PUTs a deterministic content-fingerprint
manifest (``wrm/dataset_manifest.json``) through the ``s3_resource``
``get_client().put_object`` seam: SHA256 over the exported chart values plus
per-table hashes/row counts. The resource is faked in-process
(``_FakeS3Resource``) so the suite is hermetic and the PUT ordering/failure
semantics are assertable without a bucket.

Test strategy
-------------
Execution tests run the REAL asset function against a REAL source DuckDB file
in ``tmp_path`` — no mocks of the snapshot SQL. Only the network seams are
intercepted, mirroring the repo's mock-at-seam convention
(``tests/unit/stations/test_processed.py``): a recording wrapper around
``duckdb.connect`` swallows ``INSTALL httpfs`` / ``LOAD httpfs`` /
``SET s3_*`` statements (extension download + S3 credentials) so the suite is
hermetic and offline, and swallows ``COPY ... TO s3://...`` (the S3 upload)
while capturing the target keys plus the in-flight TEMP table contents
(schema + rows) for assertions. Every other statement — both CREATE TEMP
snapshot queries and the count queries — executes for real. The wrapper also
records every executed statement, which lets us lock the S3-setup → TEMP
snapshot → counts → COPY orchestration order and the fail-before-COPY
guarantee (a broken upstream raises before any object reaches S3).

``BUCKET_NAME`` is patched to a deterministic ``test-bucket`` so the expected
keys are stable and no real bucket is ever contacted. ``s3_resource`` (required
by the asset) is supplied as a small fake exposing only
``get_client().put_object`` — the single S3 SDK seam the asset uses for the
manifest — which records each PUT (key, body, and how many ``COPY`` statements
had already run) and can be armed to raise, so upload failures are proven to
propagate.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import duckdb
import pytest
from dagster import AssetKey, MaterializeResult, build_asset_context

import wrm_pipeline.assets.dashboard as dashboard_module
from wrm_pipeline.assets.dashboard import (
    DATA_FINGERPRINT_VERSION,
    DENSITY_EXPORT_KEY,
    MANIFEST_EXPORT_KEY,
    MANIFEST_SCHEMA_VERSION,
    SNAPSHOT_PATH as DEFAULT_SNAPSHOT_PATH,
    STATIONS_EXPORT_KEY,
)
from wrm_pipeline.assets.dashboard import (
    evidence_data_snapshot,
)

# The 'evidence' marker is declared in the root pyproject.toml markers and
# conftest.py; these tests assert against the S3 export contract.
pytestmark = pytest.mark.evidence

SNAPSHOT_TS = datetime(2026, 9, 21, 12, 0, 0)

TEST_BUCKET = "test-bucket"
EXPECTED_STATIONS_KEY = f"s3://{TEST_BUCKET}/{STATIONS_EXPORT_KEY}"
EXPECTED_DENSITY_KEY = f"s3://{TEST_BUCKET}/{DENSITY_EXPORT_KEY}"
EXPECTED_MANIFEST_KEY = f"s3://{TEST_BUCKET}/{MANIFEST_EXPORT_KEY}"

STATIONS_SNAPSHOT_COLUMNS = [
    ("station_id", "VARCHAR"),
    ("name", "VARCHAR"),
    ("bikes", "BIGINT"),
    ("spaces", "BIGINT"),
    ("total_docks", "BIGINT"),
    ("installed", "BOOLEAN"),
    ("lat", "DOUBLE"),
    ("lon", "DOUBLE"),
    ("timestamp", "TIMESTAMP"),
]

DENSITY_SNAPSHOT_COLUMNS = [
    ("grid_lat", "DOUBLE"),
    ("grid_lon", "DOUBLE"),
    ("bike_count", "BIGINT"),
    ("station_count", "BIGINT"),
    ("density_per_1000m2", "BIGINT"),
]

# Network seams intercepted by the recording connection wrapper. Everything
# else runs for real against the tmp_path source database. COPY is the S3
# upload seam: swallowed (plus captured) so no real bucket is contacted.
_INTERCEPTED_PREFIXES = ("INSTALL httpfs", "LOAD httpfs", "SET s3_", "COPY ")

# Station row fixture layout: matches the recorded dashboard schema exactly.
_STATION_DDL = (
    "station_id VARCHAR, name VARCHAR, bikes BIGINT, spaces BIGINT, "
    "total_docks BIGINT, installed BOOLEAN, lat DOUBLE, lon DOUBLE, "
    "timestamp TIMESTAMP"
)
_STATION_COLUMN_COUNT = 9


def _parse_copy_target(normalized_sql: str) -> tuple[str, str]:
    """Split a normalized ``COPY <table> TO '<url>' ...`` into (table, url)."""
    parts = normalized_sql.split()
    table = parts[1]
    start = normalized_sql.find("'")
    end = normalized_sql.find("'", start + 1)
    url = normalized_sql[start + 1 : end]
    return table, url


# Fingerprinted columns mirror the exported projections (charts): every column
# of each snapshot table participates in the manifest hash.
STATIONS_FINGERPRINT_COLUMNS = [name for name, _type in STATIONS_SNAPSHOT_COLUMNS]
DENSITY_FINGERPRINT_COLUMNS = [name for name, _type in DENSITY_SNAPSHOT_COLUMNS]


class _FakeS3Client:
    """Records ``put_object`` calls; optionally raises to simulate an outage.

    The asset's only S3 SDK call is the manifest PUT
    (``get_client().put_object``); the parquet uploads go through DuckDB's
    httpfs ``COPY``, intercepted by ``_RecordingConnection``. Each recorded
    call captures ``copies_before_put`` — how many ``COPY`` statements were
    already logged when the PUT was attempted — which is how the
    ``COPY`` → ``PUT`` ordering guarantee (manifest strictly after both
    parquet exports) is asserted without relying on wall-clock timing.
    """

    def __init__(self, executed: list[str]) -> None:
        self._executed = executed
        # Set to an exception instance to arm the failure path.
        self.error: Exception | None = None
        self.put_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.put_calls.append(
            {
                "kwargs": kwargs,
                "copies_before_put": sum(
                    1 for stmt in self._executed if stmt.startswith("COPY ")
                ),
            }
        )
        if self.error is not None:
            raise self.error
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


class _FakeS3Resource:
    """Minimal stand-in for ``S3Resource``: just ``get_client()``."""

    def __init__(self, client: _FakeS3Client) -> None:
        self._client = client

    def get_client(self) -> _FakeS3Client:
        return self._client


def _scripted_datetime(monkeypatch, *instants: datetime) -> None:
    """Freeze the dashboard module's ``datetime.now()`` to scripted instants.

    ``_canonical_value`` does ``isinstance(value, (datetime, date))``, so the
    replacement must stay a real ``datetime`` subclass; only ``now`` is
    overridden. This makes ``generated_at`` controllable across runs while
    proving it never feeds ``data_hash``.
    """
    remaining = list(instants)

    class _ScriptedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: N805 - stdlib signature
            return remaining.pop(0)

    monkeypatch.setattr(dashboard_module, "datetime", _ScriptedDatetime)


class _RecordingConnection:
    """Wraps a real DuckDBPyConnection; intercepts network seams, records SQL.

    ``COPY ... TO s3://...`` statements are swallowed (never reach S3) while
    the target key and the in-flight TEMP table contents are captured into
    ``copy_keys`` / ``snapshots`` for assertions. Capture queries run directly
    against the real connection WITHOUT appending to the statement log, so
    order tests see exactly the asset's own statements.
    """

    def __init__(
        self,
        real: duckdb.DuckDBPyConnection,
        log: list[str],
        copy_keys: list[str],
        snapshots: dict,
    ) -> None:
        self._real = real
        self._log = log
        self._copy_keys = copy_keys
        self._snapshots = snapshots

    def execute(self, sql: str, *args, **kwargs):
        normalized = " ".join(sql.split())
        self._log.append(normalized)
        if normalized.startswith("COPY "):
            table, url = _parse_copy_target(normalized)
            self._copy_keys.append(url)
            try:
                columns = [
                    (r[0], r[1])
                    for r in self._real.execute(f"DESCRIBE {table};").fetchall()
                ]
                rows = self._real.execute(f"SELECT * FROM {table};").fetchall()
                self._snapshots[table] = {
                    "key": url,
                    "columns": columns,
                    "rows": rows,
                }
            except Exception:
                # Capture is best-effort diagnostics; the asset already holds
                # the real row counts for metadata. Never mask COPY semantics.
                pass
            return MagicMock(name=f"intercepted:{normalized[:40]}")
        if normalized.startswith(_INTERCEPTED_PREFIXES):
            # httpfs install/load and S3 credential SETs: not needed offline —
            # the tmp_path database holds plain tables, no S3 is contacted.
            return MagicMock(name=f"intercepted:{normalized[:40]}")
        return self._real.execute(sql, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._real.close()
        return False

    def __getattr__(self, name):
        return getattr(self._real, name)


def _row(
    station_id: str,
    name: str,
    bikes: int | None,
    lat: float | None,
    lon: float | None,
    spaces: int = 10,
    total_docks: int = 15,
    installed: bool = True,
) -> tuple:
    """Build a wrm_stations_latest row in the dashboard column order."""
    return (
        station_id,
        name,
        bikes,
        spaces,
        total_docks,
        installed,
        lat,
        lon,
        SNAPSHOT_TS,
    )


class SnapshotEnv:
    """Patched execution environment for one asset invocation."""

    def __init__(self, tmp_path, monkeypatch):
        self.source = str(tmp_path / "analytics.duckdb")
        # Local dual-write target (gitignored build artifact in prod): the
        # asset ATTACHes a sibling *.tmp and atomically renames it here.
        # Redirected under tmp_path so the write is contained and assertable.
        self.snapshot = str(tmp_path / "dashboard" / "sources" / "wrm" / "wrm.duckdb")
        self.snapshot_tmp = self.snapshot + ".tmp"
        self.executed: list[str] = []
        self.copy_keys: list[str] = []
        self.snapshots: dict = {}
        self.bucket = TEST_BUCKET
        self.stations_key = EXPECTED_STATIONS_KEY
        self.density_key = EXPECTED_DENSITY_KEY
        # Fake S3 SDK seam: only the manifest PUT goes through it.
        self.s3_client = _FakeS3Client(self.executed)
        self.s3_resource = _FakeS3Resource(self.s3_client)

        monkeypatch.setattr(dashboard_module, "db_path", self.source)
        monkeypatch.setattr(dashboard_module, "SNAPSHOT_PATH", self.snapshot)
        monkeypatch.setattr(dashboard_module, "BUCKET_NAME", self.bucket)

        real_connect = duckdb.connect

        def recording_connect(path):
            return _RecordingConnection(
                real_connect(path), self.executed, self.copy_keys, self.snapshots
            )

        monkeypatch.setattr(
            dashboard_module, "duckdb", SimpleNamespace(connect=recording_connect)
        )

    # -- source database helpers -------------------------------------------
    def seed(self, rows: list[tuple]) -> None:
        """(Re)create wrm_stations_latest with the given rows.

        ``CREATE OR REPLACE`` keeps the same ``SnapshotEnv`` reusable across
        runs (manifest determinism tests seed twice in one test).
        """
        conn = duckdb.connect(self.source)
        try:
            conn.execute(
                f"CREATE OR REPLACE TABLE wrm_stations_latest ({_STATION_DDL});"
            )
            if rows:
                placeholders = ", ".join(["?"] * _STATION_COLUMN_COUNT)
                conn.executemany(
                    f"INSERT INTO wrm_stations_latest VALUES ({placeholders});", rows
                )
        finally:
            conn.close()

    def seed_table_only(self) -> None:
        """Create an empty wrm_stations_latest table (no rows)."""
        self.seed([])

    # -- asset invocation ----------------------------------------------------
    def context(self):
        """Fresh asset context bound to the fake ``s3_resource``."""
        return build_asset_context(resources={"s3_resource": self.s3_resource})

    def run(self):
        """Execute the real asset function; return its MaterializeResult."""
        result = evidence_data_snapshot(self.context())
        assert isinstance(result, MaterializeResult)
        return result

    # -- manifest inspection (S3 SDK seam, no real upload) ------------------
    def manifest_puts(self) -> list[dict]:
        """Recorded ``put_object`` calls targeting the manifest key."""
        return [
            call
            for call in self.s3_client.put_calls
            if call["kwargs"].get("Key") == MANIFEST_EXPORT_KEY
        ]

    def manifest_bodies(self) -> list[dict]:
        """Parsed JSON bodies of the recorded manifest PUTs, in call order."""
        return [json.loads(call["kwargs"]["Body"]) for call in self.manifest_puts()]

    def manifest(self, index: int = -1) -> dict:
        """Parsed body of the ``index``-th manifest PUT (default: last)."""
        bodies = self.manifest_bodies()
        assert bodies, "asset never uploaded a dataset manifest"
        return bodies[index]

    # -- COPY-time export inspection (TEMP contents captured, no S3 read) ---
    def exported_columns(self, table: str) -> list[tuple]:
        """Schema of the TEMP table as captured at its COPY moment."""
        return self.snapshots[table]["columns"]

    def exported_rows(self, table: str) -> list[tuple]:
        """Rows of the TEMP table as captured at its COPY moment."""
        return list(self.snapshots[table]["rows"])

    def exported_key(self, table: str) -> str:
        """S3 URL the TEMP table was COPYed to."""
        return self.snapshots[table]["key"]


@pytest.fixture
def env(tmp_path, monkeypatch) -> SnapshotEnv:
    return SnapshotEnv(tmp_path, monkeypatch)


# Station set used by the grid-math happy path:
# - Alpha/Beta are 1e-6 deg (~0.11 m) apart: far below the ~31.6 m cell size
#   and offset inward from the data bounds corner, so they always share one
#   cell (no boundary straddle).
# - Gamma and Delta sit ~10+ km away in distinct cells.
# - Delta carries bikes=0 to pin the zero-density cell behavior.
GRID_ROWS = [
    _row("001", "Alpha", 5, 51.0000, 17.0000),
    _row("002", "Beta", 3, 51.000001, 17.000001),
    _row("003", "Gamma", 2, 51.1000, 17.1000, installed=False),
    _row("004", "Delta", 0, 50.9000, 16.9000),
]


class TestAssetRegistration:
    """The asset is registered with the name/group/deps the DAG expects."""

    def test_asset_key_is_evidence_data_snapshot(self):
        assert evidence_data_snapshot.keys == {AssetKey("evidence_data_snapshot")}

    def test_group_name_is_dashboard(self):
        assert evidence_data_snapshot.group_names_by_key == {
            AssetKey("evidence_data_snapshot"): "dashboard"
        }

    def test_depends_on_duckdb_enhanced_views(self):
        """Upstream dep is the enhanced-views asset (never the raw ingest)."""
        assert evidence_data_snapshot.asset_deps == {
            AssetKey("evidence_data_snapshot"): {AssetKey("duckdb_enhanced_views")}
        }

    def test_compute_kind_is_duckdb(self):
        assert evidence_data_snapshot.op.tags["dagster/compute_kind"] == "duckdb"

    def test_reexported_from_assets_package(self):
        """S9.1 wires the asset through assets.py (`from .dashboard import ...`)."""
        from wrm_pipeline.assets import evidence_data_snapshot as reexported
        from wrm_pipeline.assets import assets as assets_module

        assert reexported is evidence_data_snapshot
        assert "evidence_data_snapshot" in assets_module.__all__

    def test_default_snapshot_path_points_at_repo_dashboard_sources(self):
        """Legacy S9.1 SNAPSHOT_PATH constant still derives to the repo path.

        Still the live target: the dual-write asset writes the local snapshot
        there (see ``test_local_snapshot_file_written_atomically``).
        """
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(dashboard_module.__file__), "..", "..", "..")
        )
        assert DEFAULT_SNAPSHOT_PATH == os.path.join(
            repo_root, "dashboard", "sources", "wrm", "wrm.duckdb"
        )

    def test_export_keys_are_bucket_relative_parquet(self):
        """Both S3 object keys live under wrm/ and use parquet format."""
        assert STATIONS_EXPORT_KEY == "wrm/stations_latest.parquet"
        assert DENSITY_EXPORT_KEY == "wrm/density_grid.parquet"


class TestS3ExportMaterialization:
    """Happy path: real SQL executes, both datasets are COPYed to S3 keys."""

    def test_materialize_returns_row_and_cell_counts(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        result = env.run()

        assert result.metadata["bucket"] == TEST_BUCKET
        assert result.metadata["stations_latest_key"] == EXPECTED_STATIONS_KEY
        assert result.metadata["density_grid_key"] == EXPECTED_DENSITY_KEY
        assert result.metadata["stations_latest_rows"] == 4
        assert result.metadata["density_grid_rows"] == 3
        assert "snapshot_path" not in result.metadata

    def test_copy_targets_both_expected_keys_in_order(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        result = env.run()

        assert env.copy_keys == [result.metadata["stations_latest_key"], result.metadata["density_grid_key"]]
        assert env.copy_keys == [EXPECTED_STATIONS_KEY, EXPECTED_DENSITY_KEY]

    def test_stations_latest_is_exact_schema_projection(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        assert env.exported_columns("stations_latest") == STATIONS_SNAPSHOT_COLUMNS

    def test_density_grid_is_exact_schema(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        assert env.exported_columns("density_grid") == DENSITY_SNAPSHOT_COLUMNS

    def test_stations_latest_rows_round_trip(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        rows = sorted(env.exported_rows("stations_latest"), key=lambda r: r[0])
        assert rows == [
            ("001", "Alpha", 5, 10, 15, True, 51.0000, 17.0000, SNAPSHOT_TS),
            ("002", "Beta", 3, 10, 15, True, 51.000001, 17.000001, SNAPSHOT_TS),
            ("003", "Gamma", 2, 10, 15, False, 51.1000, 17.1000, SNAPSHOT_TS),
            ("004", "Delta", 0, 10, 15, True, 50.9000, 16.9000, SNAPSHOT_TS),
        ]

    def test_grid_cells_sum_bikes_per_1000m2(self, env: SnapshotEnv):
        """Alpha+Beta share a cell (bikes 5+3=8); Gamma and Delta are alone."""
        env.seed(GRID_ROWS)
        env.run()

        # Exported density columns: grid_lat, grid_lon, bike_count,
        # station_count, density_per_1000m2.
        cells = sorted(
            ((r[2], r[3], r[4]) for r in env.exported_rows("density_grid")),
            key=lambda c: (c[0] is None, -(c[0] or 0)),
        )
        assert cells == [
            (8, 2, 8),  # Alpha + Beta co-located
            (2, 1, 2),  # Gamma
            (0, 1, 0),  # Delta: zero bikes still forms a cell
        ]

    def test_grid_cell_centers_stay_inside_data_bounds(self, env: SnapshotEnv):
        """Cell centers stay within the data bounds plus a half-cell margin.

        The grid is floor-anchored at the data minimum (min + (i + 0.5) * delta),
        so the top cell's center may legally overshoot the data maximum by up
        to half a cell (~14 m lat / ~23 m lon here); the bottom cell's center
        always sits at min + 0.5 * delta and can never undershoot.
        """
        env.seed(GRID_ROWS)
        env.run()

        centers = [(r[0], r[1]) for r in env.exported_rows("density_grid")]

        # Mirror the grid cell sizes from the snapshot SQL:
        # lat_delta = sqrt(1000) / 111320.0 and lon_delta with cos at the
        # data's mid-latitude ((min_lat + max_lat) / 2 = (50.9 + 51.1) / 2).
        lat_delta = math.sqrt(1000) / 111320.0
        mid_lat = (50.9000 + 51.1000) / 2
        lon_delta = math.sqrt(1000) / (111320.0 * math.cos(math.radians(mid_lat)))

        assert len(centers) == 3
        for grid_lat, grid_lon in centers:
            assert 50.9000 <= grid_lat <= 51.1000 + 0.5 * lat_delta
            assert 16.9000 <= grid_lon <= 17.1000 + 0.5 * lon_delta

    def test_counts_metadata_matches_exported_contents(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        result = env.run()

        assert result.metadata["stations_latest_rows"] == len(
            env.exported_rows("stations_latest")
        )
        assert result.metadata["density_grid_rows"] == len(
            env.exported_rows("density_grid")
        )

    def test_rerun_is_idempotent(self, env: SnapshotEnv):
        """Re-running re-exports the same counts without duplicating rows."""
        env.seed(GRID_ROWS)
        first = env.run()
        second = env.run()

        assert second.metadata["stations_latest_rows"] == first.metadata[
            "stations_latest_rows"
        ]
        assert second.metadata["density_grid_rows"] == first.metadata["density_grid_rows"]
        # One COPY per dataset per run: exactly two exports each time.
        assert env.copy_keys == [
            EXPECTED_STATIONS_KEY,
            EXPECTED_DENSITY_KEY,
            EXPECTED_STATIONS_KEY,
            EXPECTED_DENSITY_KEY,
        ]
        assert len(env.exported_rows("stations_latest")) == 4
        assert len(env.exported_rows("density_grid")) == 3

    def test_unicode_station_name_survives_export(self, env: SnapshotEnv):
        unicode_name = "Świdnicka–_most 🚲"
        env.seed([_row("u1", unicode_name, 7, 51.0, 17.0)])
        env.run()

        rows = env.exported_rows("stations_latest")
        assert len(rows) == 1
        assert rows[0][1] == unicode_name

    def test_local_snapshot_file_written_atomically(self, env: SnapshotEnv):
        """Dual-write: the local wrm.duckdb exists, holds both datasets.

        Replaces the S3-only assertion that this file is never created: the
        asset ATTACHes a sibling temporary file, materializes both tables
        there, DETACHes (flush) and os.replace()s it into place — so the
        snapshot exists, both tables are readable credential-free, and no
        ``*.tmp`` may lie around afterwards.
        """
        env.seed(GRID_ROWS)
        env.run()

        assert os.path.isfile(env.snapshot)
        assert not os.path.exists(env.snapshot_tmp)
        assert any(s.startswith("ATTACH ") for s in env.executed)
        assert any(s.startswith("DETACH ") for s in env.executed)

        conn = duckdb.connect(env.snapshot, read_only=True)
        try:
            tables = {row[0] for row in conn.execute("SHOW TABLES;").fetchall()}
            assert tables == {"stations_latest", "density_grid"}
            stations = conn.execute(
                "SELECT COUNT(*) FROM stations_latest;"
            ).fetchone()[0]
            cells = conn.execute("SELECT COUNT(*) FROM density_grid;").fetchone()[0]
        finally:
            conn.close()
        assert stations == len(GRID_ROWS)
        assert cells == len(env.exported_rows("density_grid"))


class TestExportEdgeCases:
    """Degenerate inputs: empty, NULL coordinates, NULL bikes."""

    def test_empty_source_exports_two_empty_datasets_and_zero_metadata(
        self, env: SnapshotEnv
    ):
        env.seed_table_only()
        result = env.run()

        assert result.metadata["stations_latest_rows"] == 0
        assert result.metadata["density_grid_rows"] == 0
        # Empty datasets are still exported (schema present, zero rows).
        assert env.copy_keys == [EXPECTED_STATIONS_KEY, EXPECTED_DENSITY_KEY]
        assert env.exported_columns("stations_latest") == STATIONS_SNAPSHOT_COLUMNS
        assert env.exported_columns("density_grid") == DENSITY_SNAPSHOT_COLUMNS
        assert env.exported_rows("stations_latest") == []
        assert env.exported_rows("density_grid") == []

    def test_all_null_coordinates_excluded_from_grid_but_kept_in_stations(
        self, env: SnapshotEnv
    ):
        env.seed([_row("n1", "NoCoords", 4, None, None)])
        result = env.run()

        assert result.metadata["stations_latest_rows"] == 1
        assert result.metadata["density_grid_rows"] == 0

        (row,) = env.exported_rows("stations_latest")
        assert row[6] is None and row[7] is None
        assert env.exported_rows("density_grid") == []

    def test_partially_null_coordinates_only_grid_eligible_rows_aggregated(
        self, env: SnapshotEnv
    ):
        env.seed(
            [
                _row("001", "Alpha", 5, 51.0000, 17.0000),
                _row("003", "Gamma", 2, 51.1000, 17.1000),
                _row("005", "Eps", 4, None, None),  # no coords → grid-excluded
            ]
        )
        result = env.run()

        assert result.metadata["stations_latest_rows"] == 3
        assert result.metadata["density_grid_rows"] == 2

        totals = (
            sum(r[2] for r in env.exported_rows("density_grid")),
            sum(r[3] for r in env.exported_rows("density_grid")),
        )
        assert totals == (7, 2)  # Eps's 4 bikes never enter the grid

    def test_null_bikes_propagates_as_null_cell_count(self, env: SnapshotEnv):
        """Locks current behavior: bikes NULL → SUM over the cell is NULL.

        Upstream treats bikes as non-null; if this ever changes to a
        COALESCE, revisit this expectation deliberately.
        """
        env.seed([_row("n2", "NoBikes", None, 51.0, 17.0)])
        env.run()

        (row,) = env.exported_rows("density_grid")
        assert row[2] is None  # bike_count
        assert row[3] == 1  # station_count


class TestExportFailurePaths:
    """Broken upstreams must raise before any object reaches S3."""

    def test_missing_wrm_stations_latest_raises_and_copies_nothing(
        self, env: SnapshotEnv
    ):
        # Source file created empty on connect; the TEMP snapshot SELECT fails.
        with pytest.raises(duckdb.CatalogException):
            env.run()

        assert env.copy_keys == []
        assert not any(s.startswith("COPY ") for s in env.executed)
        # Fail-before-write: no ATTACH ever runs, so nothing reaches disk.
        assert not any(s.startswith("ATTACH") for s in env.executed)
        assert not os.path.exists(env.snapshot)
        # ...and no manifest is PUT for data the bucket never received.
        assert env.s3_client.put_calls == []

    def test_corrupt_snapshot_sql_raises_and_copies_nothing(
        self, env: SnapshotEnv, monkeypatch
    ):
        env.seed(GRID_ROWS)
        monkeypatch.setattr(
            dashboard_module,
            "_STATIONS_SNAPSHOT_SQL",
            "SELECT * FROM definitely_not_a_table;",
        )

        with pytest.raises(duckdb.CatalogException):
            env.run()

        # The failure happened during materialization: no S3 object written.
        assert env.copy_keys == []
        assert not any(s.startswith("COPY ") for s in env.executed)
        assert env.s3_client.put_calls == []

    def test_missing_bucket_raises_before_connect(self, env, monkeypatch):
        """BUCKET_NAME unset → ValueError before any DuckDB statement runs."""
        monkeypatch.setattr(dashboard_module, "BUCKET_NAME", "")

        with pytest.raises(ValueError, match="BUCKET_NAME"):
            evidence_data_snapshot(env.context())

        assert env.executed == []
        assert env.copy_keys == []
        assert env.s3_client.put_calls == []

    def test_missing_bucket_none_raises_before_connect(self, env, monkeypatch):
        monkeypatch.setattr(dashboard_module, "BUCKET_NAME", None)

        with pytest.raises(ValueError, match="BUCKET_NAME"):
            evidence_data_snapshot(env.context())

        assert env.executed == []
        assert env.copy_keys == []
        assert env.s3_client.put_calls == []


class TestExecutionOrder:
    """S3 setup → TEMP snapshots → counts → local snapshot → COPY ×2."""

    def test_statement_order_and_exactly_two_copies(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        sql = env.executed
        stations_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("CREATE OR REPLACE TEMP TABLE stations_latest")
        )
        density_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("CREATE OR REPLACE TEMP TABLE density_grid")
        )
        stations_count_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("SELECT COUNT(*) FROM stations_latest")
        )
        density_count_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("SELECT COUNT(*) FROM density_grid")
        )
        copy_stations_idx = next(
            i for i, s in enumerate(sql) if s.startswith("COPY stations_latest TO")
        )
        copy_density_idx = next(
            i for i, s in enumerate(sql) if s.startswith("COPY density_grid TO")
        )

        install_idx = sql.index("INSTALL httpfs;")
        load_idx = sql.index("LOAD httpfs;")
        assert install_idx < load_idx < stations_idx
        assert stations_idx < density_idx < stations_count_idx < density_count_idx
        assert density_count_idx < copy_stations_idx < copy_density_idx

        # Every S3 credential/endpoint SET precedes the TEMP snapshots.
        set_s3_idx = [i for i, s in enumerate(sql) if s.startswith("SET s3_")]
        assert set_s3_idx, "expected the S3 configuration SETs"
        assert all(i < stations_idx for i in set_s3_idx)

        # Exactly two COPYs, one per dataset, targeting the expected keys.
        copy_idx = [i for i, s in enumerate(sql) if s.startswith("COPY ")]
        assert len(copy_idx) == 2
        assert EXPECTED_STATIONS_KEY in sql[copy_stations_idx]
        assert EXPECTED_DENSITY_KEY in sql[copy_density_idx]
        assert "(FORMAT parquet)" in sql[copy_stations_idx]
        assert "(FORMAT parquet)" in sql[copy_density_idx]

        # Dual-write local-first: the snapshot ATTACH happens only once the
        # TEMP tables and their counts succeeded (a broken upstream fails
        # before any file is touched), and before the two S3 COPYs.
        attach_idx = next(
            i for i, s in enumerate(sql) if s.startswith("ATTACH ")
        )
        detach_idx = next(i for i, s in enumerate(sql) if s.startswith("DETACH "))
        assert sql[attach_idx].startswith(f"ATTACH '{env.snapshot_tmp}'")
        assert density_count_idx < attach_idx < detach_idx < copy_stations_idx

        # Both snapshot tables are materialized between ATTACH and DETACH.
        snap_stations_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("CREATE OR REPLACE TABLE snapshot_db.stations_latest")
        )
        snap_density_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("CREATE OR REPLACE TABLE snapshot_db.density_grid")
        )
        assert attach_idx < snap_stations_idx < snap_density_idx < detach_idx
        # Exactly one ATTACH/DETACH pair per run (the atomic-write lifecycle).
        assert sum(s.startswith("ATTACH ") for s in sql) == 1
        assert sum(s.startswith("DETACH ") for s in sql) == 1


class TestEndpointHandling:
    """HETZNER_ENDPOINT_URL scheme stripping (hostname-only for DuckDB)."""

    def _endpoint_set(self, env: SnapshotEnv) -> str:
        matches = [s for s in env.executed if s.startswith("SET s3_endpoint=")]
        assert len(matches) == 1
        return matches[0]

    def test_https_scheme_stripped(self, env: SnapshotEnv, monkeypatch):
        monkeypatch.setattr(
            dashboard_module, "HETZNER_ENDPOINT_URL", "https://gateway.example:9000"
        )
        monkeypatch.setattr(dashboard_module, "HETZNER_ENDPOINT", None)

        env.seed(GRID_ROWS)
        env.run()

        assert self._endpoint_set(env) == "SET s3_endpoint='gateway.example:9000';"

    def test_http_scheme_stripped(self, env: SnapshotEnv, monkeypatch):
        monkeypatch.setattr(
            dashboard_module, "HETZNER_ENDPOINT_URL", "http://gateway.example:9000"
        )
        monkeypatch.setattr(dashboard_module, "HETZNER_ENDPOINT", None)

        env.seed(GRID_ROWS)
        env.run()

        assert self._endpoint_set(env) == "SET s3_endpoint='gateway.example:9000';"

    def test_bare_hostname_preserved(self, env: SnapshotEnv, monkeypatch):
        monkeypatch.setattr(dashboard_module, "HETZNER_ENDPOINT_URL", "gateway.lan:9000")
        monkeypatch.setattr(dashboard_module, "HETZNER_ENDPOINT", None)

        env.seed(GRID_ROWS)
        env.run()

        assert self._endpoint_set(env) == "SET s3_endpoint='gateway.lan:9000';"

    def test_falls_back_to_hetzner_endpoint_when_url_empty(
        self, env: SnapshotEnv, monkeypatch
    ):
        monkeypatch.setattr(dashboard_module, "HETZNER_ENDPOINT_URL", "")
        monkeypatch.setattr(
            dashboard_module, "HETZNER_ENDPOINT", "http://fallback.example:9000"
        )

        env.seed(GRID_ROWS)
        env.run()

        assert self._endpoint_set(env) == "SET s3_endpoint='fallback.example:9000';"

    def test_missing_config_degrades_to_empty_endpoint(self, env, monkeypatch):
        """No endpoint configured anywhere → empty SET, no crash (sane default)."""
        monkeypatch.setattr(dashboard_module, "HETZNER_ENDPOINT_URL", None)
        monkeypatch.setattr(dashboard_module, "HETZNER_ENDPOINT", None)

        env.seed(GRID_ROWS)
        result = env.run()

        assert self._endpoint_set(env) == "SET s3_endpoint='';"
        assert result.metadata["stations_latest_rows"] == 4
        assert env.copy_keys == [EXPECTED_STATIONS_KEY, EXPECTED_DENSITY_KEY]


# Scripted wall-clock instants for manifest determinism tests: same data must
# hash identically even though ``generated_at`` differs between runs.
MANIFEST_TS_ONE = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
MANIFEST_TS_TWO = datetime(2026, 9, 21, 12, 15, 0, tzinfo=timezone.utc)


class TestDatasetManifest:
    """Content-fingerprint manifest: PUT target, order, fields, key set."""

    def test_manifest_put_targets_expected_key_after_both_parquet_exports(
        self, env: SnapshotEnv
    ):
        env.seed(GRID_ROWS)
        env.run()

        puts = env.manifest_puts()
        assert len(puts) == 1
        call = puts[0]
        assert call["kwargs"]["Bucket"] == TEST_BUCKET
        assert call["kwargs"]["Key"] == MANIFEST_EXPORT_KEY
        assert call["kwargs"]["ContentType"] == "application/json"
        assert EXPECTED_MANIFEST_KEY == f"s3://{TEST_BUCKET}/{MANIFEST_EXPORT_KEY}"

        # Both parquet COPYs had already been executed when the PUT ran...
        assert call["copies_before_put"] == 2
        assert env.copy_keys == [EXPECTED_STATIONS_KEY, EXPECTED_DENSITY_KEY]
        # ...and the manifest is an SDK PUT, never a third DuckDB COPY.
        assert sum(1 for s in env.executed if s.startswith("COPY ")) == 2

    def test_manifest_body_has_required_fields_and_row_counts(
        self, env: SnapshotEnv
    ):
        env.seed(GRID_ROWS)
        result = env.run()

        manifest = env.manifest()
        assert set(manifest) == {
            "schema_version",
            "fingerprint_version",
            "data_hash",
            "generated_at",
            "export_keys",
            "tables",
        }
        assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
        assert manifest["fingerprint_version"] == DATA_FINGERPRINT_VERSION
        assert manifest["export_keys"] == [STATIONS_EXPORT_KEY, DENSITY_EXPORT_KEY]

        # Hashes are lowercase 64-char SHA256 digests.
        assert len(manifest["data_hash"]) == 64
        int(manifest["data_hash"], 16)
        # generated_at is an ISO-8601, timezone-aware timestamp.
        assert datetime.fromisoformat(manifest["generated_at"]).tzinfo is not None

        # The materialization metadata mirrors the manifest.
        assert result.metadata["data_hash"] == manifest["data_hash"]
        assert result.metadata["dataset_manifest_key"] == EXPECTED_MANIFEST_KEY
        assert (
            result.metadata["dataset_manifest_schema_version"]
            == MANIFEST_SCHEMA_VERSION
        )

        tables = manifest["tables"]
        assert set(tables) == {"stations_latest", "density_grid"}
        expected = {
            "stations_latest": (
                STATIONS_EXPORT_KEY,
                STATIONS_FINGERPRINT_COLUMNS,
                4,
            ),
            "density_grid": (DENSITY_EXPORT_KEY, DENSITY_FINGERPRINT_COLUMNS, 3),
        }
        for table, (export_key, columns, row_count) in expected.items():
            entry = tables[table]
            assert entry["export_key"] == export_key
            assert entry["columns"] == columns
            assert entry["row_count"] == row_count
            assert entry["row_count"] == result.metadata[f"{table}_rows"]
            assert len(entry["table_hash"]) == 64
            int(entry["table_hash"], 16)

    def test_manifest_body_is_canonical_json_with_trailing_newline(
        self, env: SnapshotEnv
    ):
        env.seed(GRID_ROWS)
        env.run()

        raw = env.manifest_puts()[0]["kwargs"]["Body"]
        assert isinstance(raw, bytes)
        assert raw.endswith(b"\n")
        text = raw.decode("utf-8")
        assert text == json.dumps(
            json.loads(text), sort_keys=True, separators=(",", ":")
        ) + "\n"

    def test_empty_datasets_still_produce_manifest_hashes(self, env: SnapshotEnv):
        env.seed_table_only()
        result = env.run()

        manifest = env.manifest()
        assert manifest["tables"]["stations_latest"]["row_count"] == 0
        assert manifest["tables"]["density_grid"]["row_count"] == 0
        assert len(manifest["data_hash"]) == 64
        assert result.metadata["data_hash"] == manifest["data_hash"]


class TestManifestDeterminism:
    """Identical chart values hash identically; changes move the digest."""

    def test_identical_data_produces_identical_hashes(
        self, env: SnapshotEnv, monkeypatch
    ):
        _scripted_datetime(monkeypatch, MANIFEST_TS_ONE, MANIFEST_TS_TWO)
        env.seed(GRID_ROWS)

        first = env.run()
        second = env.run()

        first_manifest = env.manifest(0)
        second_manifest = env.manifest(1)
        assert first_manifest["generated_at"] == MANIFEST_TS_ONE.isoformat()
        assert second_manifest["generated_at"] == MANIFEST_TS_TWO.isoformat()

        # generated_at is metadata only and never feeds the fingerprint.
        assert second_manifest["data_hash"] == first_manifest["data_hash"]
        assert second.metadata["data_hash"] == first.metadata["data_hash"]
        for table in ("stations_latest", "density_grid"):
            assert (
                second_manifest["tables"][table]["table_hash"]
                == first_manifest["tables"][table]["table_hash"]
            )

        # One manifest PUT per materialization.
        assert len(env.manifest_puts()) == 2

    def test_row_insertion_order_does_not_change_hash(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        baseline = env.run().metadata["data_hash"]
        baseline_manifest = env.manifest(0)

        env.seed(list(reversed(GRID_ROWS)))
        reordered = env.run()
        reordered_manifest = env.manifest(1)

        assert reordered.metadata["data_hash"] == baseline
        for table in ("stations_latest", "density_grid"):
            assert (
                reordered_manifest["tables"][table]["table_hash"]
                == baseline_manifest["tables"][table]["table_hash"]
            )

    def test_chart_value_change_changes_data_hash(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        baseline = env.run()
        baseline_manifest = env.manifest(0)

        changed_rows = list(GRID_ROWS)
        changed_rows[0] = _row("001", "Alpha", 4, 51.0000, 17.0000)  # 5 -> 4 bikes
        env.seed(changed_rows)
        changed = env.run()
        changed_manifest = env.manifest(1)

        assert changed.metadata["data_hash"] != baseline.metadata["data_hash"]
        assert changed_manifest["data_hash"] != baseline_manifest["data_hash"]
        # The changed chart value moves BOTH datasets: the station row and the
        # shared Alpha+Beta cell (8 -> 7 bikes).
        for table in ("stations_latest", "density_grid"):
            assert (
                changed_manifest["tables"][table]["table_hash"]
                != baseline_manifest["tables"][table]["table_hash"]
            )
        # Shape is unchanged: the digest moved because content moved.
        assert changed_manifest["tables"]["stations_latest"]["row_count"] == 4
        assert changed_manifest["tables"]["density_grid"]["row_count"] == 3

    def test_non_grid_field_change_only_moves_stations_hash(
        self, env: SnapshotEnv
    ):
        """Per-table hashes are independent: a rename never moves the grid."""
        env.seed(GRID_ROWS)
        env.run()
        baseline = env.manifest(0)

        renamed = list(GRID_ROWS)
        renamed[0] = _row("001", "Alpha Renamed", 5, 51.0000, 17.0000)
        env.seed(renamed)
        env.run()
        changed = env.manifest(1)

        assert (
            changed["tables"]["stations_latest"]["table_hash"]
            != baseline["tables"]["stations_latest"]["table_hash"]
        )
        # density_grid aggregates coords/bikes only, so the rename is invisible.
        assert (
            changed["tables"]["density_grid"]["table_hash"]
            == baseline["tables"]["density_grid"]["table_hash"]
        )
        # The combined digest still changes (stations contributed to it).
        assert changed["data_hash"] != baseline["data_hash"]


class TestManifestUploadFailure:
    """A failed manifest PUT must fail the run, never be swallowed."""

    def test_put_failure_propagates_after_both_parquet_exports(
        self, env: SnapshotEnv
    ):
        env.seed(GRID_ROWS)
        error = RuntimeError("manifest upload failed: bucket unavailable")
        env.s3_client.error = error

        with pytest.raises(RuntimeError) as excinfo:
            env.run()

        # The original error object propagates unwrapped.
        assert excinfo.value is error
        # Both parquet COPYs had already completed; the PUT was last.
        assert env.copy_keys == [EXPECTED_STATIONS_KEY, EXPECTED_DENSITY_KEY]
        puts = env.manifest_puts()
        assert len(puts) == 1
        assert puts[0]["copies_before_put"] == 2
        # Local-first contract: the snapshot written before any upload remains.
        assert os.path.isfile(env.snapshot)

    def test_put_failure_is_attempted_once_and_not_swallowed(
        self, env: SnapshotEnv
    ):
        env.seed(GRID_ROWS)
        env.s3_client.error = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            env.run()

        assert len(env.s3_client.put_calls) == 1
