"""Unit tests for the dashboard ``evidence_data_snapshot`` asset (S9.1).

Contract under test
-------------------
``wrm_pipeline/assets/dashboard.py`` materializes two analytics tables into a
static, credential-free DuckDB file at
``dashboard/sources/wrm/wrm.duckdb``:

- ``stations_latest`` — projection of the ``wrm_stations_latest`` enhanced view
  onto the dashboard schema (station_id, name, bikes, spaces, total_docks,
  installed, lat, lon, timestamp).
- ``density_grid`` — 1000 m² grid aggregation (SQL port of
  ``bike_spatial_density_analysis._analyze_grid_density``): bikes summed per
  ~31.6 m cell, cell centers emitted as grid_lat/grid_lon.

Test strategy
-------------
Execution tests run the REAL asset function against REAL DuckDB files in
``tmp_path`` — no mocks of the snapshot SQL. Only the network seams are
intercepted, mirroring the repo's mock-at-seam convention
(``tests/unit/stations/test_processed.py``): a recording wrapper around
``duckdb.connect`` swallows ``INSTALL httpfs`` / ``LOAD httpfs`` /
``SET s3_*`` statements (extension download + S3 credentials) so the suite is
hermetic and offline, while every other statement — ATTACH, both CREATE
OR REPLACE TABLE snapshot queries, the count queries and DETACH — executes
for real. The wrapper also records every executed statement, which lets us
lock the S3-setup → ATTACH → snapshot → DETACH orchestration order and the
``finally: DETACH`` guarantee.

The recorded S9.1 stub artifact itself (``dashboard/sources/wrm/wrm.duckdb``,
schema-only, both tables empty) is asserted separately in
``test_dashboard_scaffold.py``; these tests never touch that file (paths are
redirected to ``tmp_path``).
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import duckdb
import pytest
from dagster import AssetKey, MaterializeResult, build_asset_context

import wrm_pipeline.assets.dashboard as dashboard_module
from wrm_pipeline.assets.dashboard import (
    SNAPSHOT_PATH as DEFAULT_SNAPSHOT_PATH,
)
from wrm_pipeline.assets.dashboard import (
    evidence_data_snapshot,
)

# The 'evidence' marker is declared in the root pyproject.toml markers and
# conftest.py; these tests assert against the recorded S9.1 contract.
pytestmark = pytest.mark.evidence

SNAPSHOT_TS = datetime(2026, 9, 21, 12, 0, 0)

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
# else runs for real against the tmp_path databases.
_INTERCEPTED_PREFIXES = ("INSTALL httpfs", "LOAD httpfs", "SET s3_")

# Station row fixture layout: matches the recorded dashboard schema exactly.
_STATION_DDL = (
    "station_id VARCHAR, name VARCHAR, bikes BIGINT, spaces BIGINT, "
    "total_docks BIGINT, installed BOOLEAN, lat DOUBLE, lon DOUBLE, "
    "timestamp TIMESTAMP"
)
_STATION_COLUMN_COUNT = 9


class _RecordingConnection:
    """Wraps a real DuckDBPyConnection; intercepts network seams, records SQL."""

    def __init__(self, real: duckdb.DuckDBPyConnection, log: list[str]) -> None:
        self._real = real
        self._log = log

    def execute(self, sql: str, *args, **kwargs):
        normalized = " ".join(sql.split())
        self._log.append(normalized)
        if normalized.startswith(_INTERCEPTED_PREFIXES):
            # httpfs install/load and S3 credential SETs: not needed offline —
            # the tmp_path databases hold plain tables, no S3 is contacted.
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
        self.snapshot = str(tmp_path / "dashboard" / "sources" / "wrm" / "wrm.duckdb")
        self.executed: list[str] = []

        monkeypatch.setattr(dashboard_module, "db_path", self.source)
        monkeypatch.setattr(dashboard_module, "SNAPSHOT_PATH", self.snapshot)

        real_connect = duckdb.connect

        def recording_connect(path):
            return _RecordingConnection(real_connect(path), self.executed)

        monkeypatch.setattr(
            dashboard_module, "duckdb", SimpleNamespace(connect=recording_connect)
        )

    # -- source database helpers -------------------------------------------
    def seed(self, rows: list[tuple]) -> None:
        """Create the wrm_stations_latest table with the given rows."""
        conn = duckdb.connect(self.source)
        try:
            conn.execute(
                f"CREATE TABLE wrm_stations_latest ({_STATION_DDL});"
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
    def run(self):
        """Execute the real asset function; return its MaterializeResult."""
        result = evidence_data_snapshot(build_asset_context())
        assert isinstance(result, MaterializeResult)
        return result

    # -- snapshot inspection -------------------------------------------------
    def open_snapshot(self) -> duckdb.DuckDBPyConnection:
        """Open the materialized snapshot file read-only.

        Note: inside the asset the tables are addressed via the ATTACH alias
        (``wrm_dash.*``); when the file is opened directly they live in the
        default (main) schema — exactly as the recorded S9.1 stub shows.
        """
        return duckdb.connect(self.snapshot, read_only=True)


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
        """SNAPSHOT_PATH derives to <repo>/dashboard/sources/wrm/wrm.duckdb."""
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(dashboard_module.__file__), "..", "..", "..")
        )
        assert DEFAULT_SNAPSHOT_PATH == os.path.join(
            repo_root, "dashboard", "sources", "wrm", "wrm.duckdb"
        )


class TestSnapshotMaterialization:
    """Happy path: real SQL executes, both tables land in the snapshot file."""

    def test_materialize_returns_row_and_cell_counts(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        result = env.run()

        assert result.metadata["snapshot_path"] == env.snapshot
        assert result.metadata["stations_latest_rows"] == 4
        assert result.metadata["density_grid_rows"] == 3

    def test_snapshot_file_contains_both_tables(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        with env.open_snapshot() as snap:
            assert set(snap.execute("SHOW TABLES").fetchall()) == {
                ("density_grid",),
                ("stations_latest",),
            }

    def test_stations_latest_is_exact_schema_projection(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        with env.open_snapshot() as snap:
            described = [
                (r[0], r[1]) for r in snap.execute("DESCRIBE stations_latest").fetchall()
            ]
        assert described == STATIONS_SNAPSHOT_COLUMNS

    def test_density_grid_is_exact_schema(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        with env.open_snapshot() as snap:
            described = [
                (r[0], r[1]) for r in snap.execute("DESCRIBE density_grid").fetchall()
            ]
        assert described == DENSITY_SNAPSHOT_COLUMNS

    def test_stations_latest_rows_round_trip(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        with env.open_snapshot() as snap:
            rows = snap.execute(
                "SELECT station_id, name, bikes, spaces, total_docks, installed, "
                "lat, lon, timestamp FROM stations_latest ORDER BY station_id;"
            ).fetchall()

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

        with env.open_snapshot() as snap:
            cells = snap.execute(
                "SELECT bike_count, station_count, density_per_1000m2 "
                "FROM density_grid ORDER BY bike_count DESC;"
            ).fetchall()

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

        with env.open_snapshot() as snap:
            centers = snap.execute(
                "SELECT grid_lat, grid_lon FROM density_grid;"
            ).fetchall()

        # Mirror the grid cell sizes from the snapshot SQL:
        # lat_delta = sqrt(1000) / 111320.0 and lon_delta with cos at the
        # data's mid-latitude ((min_lat + max_lat) / 2 = (50.9 + 51.1) / 2).
        lat_delta = math.sqrt(1000) / 111320.0
        mid_lat = (50.9000 + 51.1000) / 2
        lon_delta = math.sqrt(1000) / (111320.0 * math.cos(math.radians(mid_lat)))

        for grid_lat, grid_lon in centers:
            assert 50.9000 <= grid_lat <= 51.1000 + 0.5 * lat_delta
            assert 16.9000 <= grid_lon <= 17.1000 + 0.5 * lon_delta

    def test_counts_metadata_matches_snapshot_contents(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        result = env.run()

        with env.open_snapshot() as snap:
            stations = snap.execute("SELECT COUNT(*) FROM stations_latest").fetchone()[0]
            cells = snap.execute("SELECT COUNT(*) FROM density_grid").fetchone()[0]

        assert result.metadata["stations_latest_rows"] == stations
        assert result.metadata["density_grid_rows"] == cells

    def test_rerun_is_idempotent(self, env: SnapshotEnv):
        """CREATE OR REPLACE + ATTACH over an existing file must not duplicate."""
        env.seed(GRID_ROWS)
        first = env.run()
        second = env.run()

        assert second.metadata["stations_latest_rows"] == first.metadata[
            "stations_latest_rows"
        ]
        assert second.metadata["density_grid_rows"] == first.metadata["density_grid_rows"]

        with env.open_snapshot() as snap:
            assert snap.execute("SELECT COUNT(*) FROM stations_latest").fetchone()[0] == 4
            assert snap.execute("SELECT COUNT(*) FROM density_grid").fetchone()[0] == 3

    def test_unicode_station_name_survives_snapshot(self, env: SnapshotEnv):
        unicode_name = "Świdnicka–_most 🚲"
        env.seed([_row("u1", unicode_name, 7, 51.0, 17.0)])
        env.run()

        with env.open_snapshot() as snap:
            (name,) = snap.execute(
                "SELECT name FROM stations_latest WHERE station_id = 'u1';"
            ).fetchone()
        assert name == unicode_name


class TestSnapshotEdgeCases:
    """Degenerate inputs: empty, NULL coordinates, NULL bikes."""

    def test_empty_source_yields_two_empty_tables_and_zero_metadata(
        self, env: SnapshotEnv
    ):
        env.seed_table_only()
        result = env.run()

        assert result.metadata["stations_latest_rows"] == 0
        assert result.metadata["density_grid_rows"] == 0

        with env.open_snapshot() as snap:
            assert set(snap.execute("SHOW TABLES").fetchall()) == {
                ("density_grid",),
                ("stations_latest",),
            }
            assert snap.execute("SELECT COUNT(*) FROM stations_latest").fetchone()[0] == 0
            assert snap.execute("SELECT COUNT(*) FROM density_grid").fetchone()[0] == 0

    def test_all_null_coordinates_excluded_from_grid_but_kept_in_stations(
        self, env: SnapshotEnv
    ):
        env.seed([_row("n1", "NoCoords", 4, None, None)])
        result = env.run()

        assert result.metadata["stations_latest_rows"] == 1
        assert result.metadata["density_grid_rows"] == 0

        with env.open_snapshot() as snap:
            (lat, lon) = snap.execute(
                "SELECT lat, lon FROM stations_latest;"
            ).fetchone()
            assert lat is None and lon is None

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

        with env.open_snapshot() as snap:
            totals = snap.execute(
                "SELECT SUM(bike_count), SUM(station_count) FROM density_grid;"
            ).fetchone()
        assert totals == (7, 2)  # Eps's 4 bikes never enter the grid

    def test_null_bikes_propagates_as_null_cell_count(self, env: SnapshotEnv):
        """Locks current behavior: bikes NULL → SUM over the cell is NULL.

        Upstream treats bikes as non-null; if this ever changes to a
        COALESCE, revisit this expectation deliberately.
        """
        env.seed([_row("n2", "NoBikes", None, 51.0, 17.0)])
        env.run()

        with env.open_snapshot() as snap:
            bike_count, station_count = snap.execute(
                "SELECT bike_count, station_count FROM density_grid;"
            ).fetchone()
        assert bike_count is None
        assert station_count == 1

    def test_snapshot_path_directory_created_on_demand(self, env: SnapshotEnv):
        """os.makedirs(exist_ok=True) creates dashboard/sources/wrm from nothing."""
        env.seed(GRID_ROWS)
        env.run()
        assert os.path.isfile(env.snapshot)


class TestSnapshotFailurePaths:
    """Broken upstreams must raise and still DETACH (finally semantics)."""

    def test_missing_wrm_stations_latest_raises_and_detaches(self, env: SnapshotEnv):
        # Connect creates an empty db file; the snapshot SELECT then fails.
        with pytest.raises(duckdb.CatalogException):
            env.run()

        assert env.executed[-1].startswith("DETACH wrm_dash;")

    def test_failed_snapshot_leaves_no_partial_tables(self, env: SnapshotEnv):
        """A failed CREATE must not leave wrm_dash data in the snapshot file."""
        # Fresh empty source db: ATTACH creates the file, CREATE then fails.
        with pytest.raises(duckdb.CatalogException):
            env.run()

        with env.open_snapshot() as snap:
            assert snap.execute("SHOW TABLES").fetchall() == []

    def test_corrupt_snapshot_sql_raises_and_still_detaches(
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

        # finally: DETACH runs even though the CREATE blew up.
        assert env.executed[-1].startswith("DETACH wrm_dash;")
        # And the pre-existing snapshot file was not left with partial data.
        with env.open_snapshot() as snap:
            assert snap.execute("SHOW TABLES").fetchall() == []

    def test_unwritable_snapshot_parent_raises_before_attach(self, env, monkeypatch):
        """SNAPSHOT_PATH under a file (not a directory) fails at makedirs."""
        blocker = os.path.join(os.path.dirname(env.snapshot) + "_blocked")
        os.makedirs(blocker, exist_ok=True)
        with open(os.path.join(blocker, "wrm"), "w") as fh:
            fh.write("not a directory")

        monkeypatch.setattr(
            dashboard_module, "SNAPSHOT_PATH", os.path.join(blocker, "wrm", "wrm.duckdb")
        )

        with pytest.raises((NotADirectoryError, FileExistsError, OSError)):
            evidence_data_snapshot(build_asset_context())

        # Failure happened before any ATTACH: source db never opened.
        assert not any(s.startswith("ATTACH") for s in env.executed)


class TestExecutionOrder:
    """S3 setup → ATTACH → snapshots → counts → DETACH, exactly once."""

    def test_statement_order_and_single_detach(self, env: SnapshotEnv):
        env.seed(GRID_ROWS)
        env.run()

        sql = env.executed
        attach_idx = next(i for i, s in enumerate(sql) if s.startswith("ATTACH"))
        stations_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("CREATE OR REPLACE TABLE wrm_dash.stations_latest")
        )
        density_idx = next(
            i
            for i, s in enumerate(sql)
            if s.startswith("CREATE OR REPLACE TABLE wrm_dash.density_grid")
        )

        install_idx = sql.index("INSTALL httpfs;")
        load_idx = sql.index("LOAD httpfs;")
        assert install_idx < load_idx < attach_idx
        assert attach_idx < stations_idx < density_idx

        # Every S3 credential/endpoint SET precedes the ATTACH.
        set_s3_idx = [i for i, s in enumerate(sql) if s.startswith("SET s3_")]
        assert set_s3_idx, "expected the S3 configuration SETs"
        assert all(i < attach_idx for i in set_s3_idx)

        # DETACH is final and runs exactly once.
        assert sql[-1].startswith("DETACH wrm_dash;")
        assert sum(s.startswith("DETACH wrm_dash;") for s in sql) == 1


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