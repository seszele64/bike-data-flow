"""Focused tests for the DuckDB enhanced-view asset (S8).

Contract under test
-------------------
``create_enhanced_views.py`` creates five analytics views over the enhanced
WRM station archive. The production change under test splits the former
single ``wrm_stations_latest`` view in two:

- ``wrm_stations_latest`` now reads the **stable, unpartitioned** enhanced
  latest snapshot (``enhanced/latest/all_enhanced_latest.parquet`` written by
  ``wrm_stations_enhanced_data_latest_asset``), keeps only
  ``record_type = 'station'`` rows, and orders by ``station_id``. It performs
  **no** historical ranking.
- ``wrm_stations_latest_history`` retains the former behaviour: a
  ``ROW_NUMBER()`` window over the full enhanced archive, partitioned by
  ``station_id`` and ordered by ``date DESC, file_timestamp DESC``, keeping
  ``rn = 1``.

Downstream dashboard/density/summary SQL still consumes ``wrm_stations_latest``
and must never be pointed at the *_history* view.

Test strategy
-------------
Two complementary seams, following the repo's existing DuckDB test pattern
(``tests/unit/dashboard/test_evidence_data_snapshot.py``):

1. **DDL contract** — a recording connection (no real DuckDB) captures the
   emitted SQL, so the view names, source object keys, filters and ordering are
   asserted exactly, plus the tolerance of an as-yet-unwritten latest snapshot
   and the fail-fast behaviour of a broken archive.
2. **Behaviour** — the *real* asset runs against a real in-memory DuckDB with
   the S3 URIs rewritten to tmp_path parquet fixtures (httpfs ``INSTALL``/
   ``LOAD``/``SET s3_*`` are swallowed). The emitted DDL executes for real, so
   ranking/dedup/filter semantics are verified, not just string-matched.
"""

from __future__ import annotations

import inspect
import re
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import duckdb
import pandas as pd
import pytest

from wrm_pipeline.assets.duckdb import create_enhanced_views as views_module
from wrm_pipeline.assets.duckdb.create_enhanced_views import (
    create_duckdb_enhanced_views,
)
from wrm_pipeline.assets.stations.latest_all import (
    WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
    wrm_stations_enhanced_data_latest_asset,
)
from wrm_pipeline.config import WRM_STATIONS_S3_PREFIX

TEST_BUCKET = "test-bucket"

# Invoke the asset's compute function directly: the asset declares no inputs
# and only touches ``context.log``, so a mock context is sufficient and keeps
# Dagster's ephemeral-instance machinery out of the test loop.
_COMPUTE_FN = create_duckdb_enhanced_views.op.compute_fn.decorated_fn

ARCHIVE_URL = (
    f"s3://{TEST_BUCKET}/{WRM_STATIONS_S3_PREFIX}enhanced/all/**/*.parquet"
)
LATEST_URL = (
    f"s3://{TEST_BUCKET}/{WRM_STATIONS_S3_PREFIX}"
    "enhanced/latest/all_enhanced_latest.parquet"
)

EXPECTED_VIEWS = [
    "wrm_stations_enhanced_data",
    "wrm_stations_only",
    "wrm_bikes_only",
    "wrm_stations_latest_history",
    "wrm_stations_latest",
]


# ---------------------------------------------------------------------------
# Recording connection (S3 seams intercepted, view DDL recorded)
# ---------------------------------------------------------------------------


class _Result:
    """Minimal DB-API-ish result for statements swallowed in fake mode."""

    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = list(rows) if rows is not None else ([] if row is None else [row])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _RecordingConnection:
    """Records every statement; optionally executes view DDL for real.

    ``INSTALL httpfs`` / ``LOAD httpfs`` / ``SET s3_*`` never reach DuckDB (no
    extension download, no credentials). When ``real`` is a DuckDB connection,
    S3 parquet URLs are rewritten to local fixtures and every other statement
    executes for real, so emitted DDL semantics are exercised offline.
    """

    def __init__(self, real, statements, path_map=None, latest_probe_error=None):
        self._real = real
        self._statements = statements
        self._path_map = path_map or {}
        self._latest_probe_error = latest_probe_error

    def execute(self, sql, *args, **kwargs):
        normalized = " ".join(sql.split())
        self._statements.append(normalized)

        if normalized.startswith(("INSTALL ", "LOAD ")):
            return _Result()
        if normalized.startswith("SET s3_"):
            return _Result()

        if (
            self._latest_probe_error is not None
            and normalized.startswith("SELECT COUNT(*) FROM read_parquet(")
            and LATEST_URL in normalized
        ):
            raise self._latest_probe_error

        if self._real is not None:
            rewritten = normalized
            for url, local in self._path_map.items():
                rewritten = rewritten.replace(url, str(local))
            return self._real.execute(rewritten, *args, **kwargs)

        return self._canned(normalized)

    @staticmethod
    def _canned(normalized: str) -> _Result:
        """Offline answers for the asset's probe/verification statements."""
        if normalized.startswith("SHOW TABLES"):
            return _Result(rows=[])
        if "FROM glob(" in normalized:
            return _Result(rows=[])
        if "GROUP BY record_type" in normalized:
            return _Result(rows=[])
        if normalized.startswith("SELECT COUNT(*)"):
            return _Result(row=(0,))
        return _Result()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        # The real connection is owned by the harness so behaviour tests can
        # still query the created views after the asset returns.
        return False


class Harness:
    """Runs the real asset with ``duckdb.connect`` replaced at the module seam."""

    def __init__(self, monkeypatch, *, path_map=None, latest_probe_error=None):
        self.statements: list[str] = []
        self.context = SimpleNamespace(log=Mock())
        self.conn = None
        self._real_mode = path_map is not None
        self._path_map = path_map or {}
        self._latest_probe_error = latest_probe_error

        monkeypatch.setattr(views_module, "BUCKET_NAME", TEST_BUCKET)
        monkeypatch.setattr(views_module, "db_path", ":memory:")
        # The endpoint must be a string: production reads it from env, absent
        # in CI. The hostname-stripping path is not what these tests cover.
        monkeypatch.setattr(
            views_module, "HETZNER_ENDPOINT_URL", "https://s3.example.test"
        )
        monkeypatch.setattr(
            views_module, "duckdb", type("FakeDuckDB", (), {"connect": self._connect})
        )

    def _connect(self, path):
        real = None
        if self._real_mode:
            real = duckdb.connect(":memory:")
            self.conn = real
        return _RecordingConnection(
            real, self.statements, self._path_map, self._latest_probe_error
        )

    def run(self):
        return _COMPUTE_FN(self.context)

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FRAME_COLUMNS = [
    "station_id",
    "record_type",
    "date",
    "file_timestamp",
    "name",
    "bikes",
]

# Archive rows: 001 and 003 have two station snapshots each, and every station
# also has (or is accompanied by) a bike row that the views must ignore.
ARCHIVE_ROWS = [
    ("001", "station", "2024-01-15", "2024-01-15 10:30:00", "S1-archive-old", 1),
    ("001", "station", "2024-01-16", "2024-01-16 09:00:00", "S1-archive-new", 2),
    ("001", "bike", "2024-01-16", "2024-01-16 09:30:00", "BIKE 1", 0),
    ("002", "station", "2024-01-16", "2024-01-16 08:00:00", "S2", 5),
    ("003", "station", "2024-01-16", "2024-01-16 08:00:00", "S3-archive-old", 6),
    ("003", "station", "2024-01-16", "2024-01-16 08:30:00", "S3-archive-new", 7),
]

# Latest snapshot rows: 003 arrives with an OLDER date than its archive rows to
# prove the latest view trusts the snapshot rather than ranking history. 002 is
# a bike-only row here, so it must not surface in wrm_stations_latest.
LATEST_ROWS = [
    ("001", "station", "2024-01-17", "2024-01-17 12:00:00", "S1-snapshot", 4),
    ("002", "bike", "2024-01-17", "2024-01-17 12:00:00", "BIKE 2", 0),
    ("003", "station", "2024-01-14", "2024-01-14 12:00:00", "S3-snapshot-stale", 3),
]


def _frame(rows) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=FRAME_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["file_timestamp"] = pd.to_datetime(frame["file_timestamp"])
    return frame


def _view_ddl(statements: list[str], view: str) -> str:
    marker = f"CREATE OR REPLACE VIEW {view} AS"
    matches = [statement for statement in statements if marker in statement]
    assert len(matches) == 1, (
        f"expected exactly one CREATE VIEW for {view}, got {len(matches)}: "
        f"{matches}"
    )
    return matches[0]


def _created_views(statements: list[str]) -> list[str]:
    joined = " ".join(statements)
    return re.findall(r"CREATE OR REPLACE VIEW (\w+) AS", joined)


class BehaviorHarness(Harness):
    """Real DuckDB + local parquet fixtures; S3 URLs rewritten to tmp_path."""

    def __init__(self, tmp_path, monkeypatch):
        self.archive_dir = tmp_path / "archive"
        self.latest_file = tmp_path / "all_enhanced_latest.parquet"
        self.archive_glob = str(self.archive_dir / "**" / "*.parquet")
        super().__init__(
            monkeypatch,
            path_map={ARCHIVE_URL: self.archive_glob, LATEST_URL: str(self.latest_file)},
        )

    def seed(self, latest_rows=None) -> None:
        part = self.archive_dir / "dt=2024-01-15" / "part.parquet"
        part.parent.mkdir(parents=True, exist_ok=True)
        _frame(ARCHIVE_ROWS).to_parquet(part, index=False)
        _frame(LATEST_ROWS if latest_rows is None else latest_rows).to_parquet(
            self.latest_file, index=False
        )


@pytest.fixture
def ddl(monkeypatch):
    """Asset executed with a recording-only connection; DDL captured."""
    harness = Harness(monkeypatch)
    harness.run()
    return harness


@pytest.fixture
def behavior(tmp_path, monkeypatch):
    """Asset executed against a real in-memory DuckDB over parquet fixtures."""
    harness = BehaviorHarness(tmp_path, monkeypatch)
    harness.seed()
    harness.run()
    try:
        yield harness
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# wrm_stations_latest: DDL contract
# ---------------------------------------------------------------------------


class TestLatestViewDdl:
    def test_reads_the_stable_unpartitioned_latest_snapshot(self, ddl):
        ddl_sql = _view_ddl(ddl.statements, "wrm_stations_latest")

        assert f"read_parquet('{LATEST_URL}')" in ddl_sql
        # It must not fall back to the partitioned archive or the ranked view.
        assert ARCHIVE_URL not in ddl_sql
        assert "enhanced/all/" not in ddl_sql
        assert "wrm_stations_enhanced_data" not in ddl_sql

    def test_filters_station_rows_and_orders_by_station_id(self, ddl):
        ddl_sql = _view_ddl(ddl.statements, "wrm_stations_latest")

        assert "WHERE record_type = 'station'" in ddl_sql
        assert "ORDER BY station_id" in ddl_sql

    def test_does_not_rank_history(self, ddl):
        """The latest view must be a straight snapshot read, not the old rank."""
        ddl_sql = _view_ddl(ddl.statements, "wrm_stations_latest")

        assert "ROW_NUMBER" not in ddl_sql.upper()
        assert "PARTITION BY" not in ddl_sql.upper()
        assert "rn = 1" not in ddl_sql

    def test_source_key_matches_the_latest_asset_write_contract(self, ddl):
        """The view reads exactly the key ``latest_all`` writes (no drift)."""
        assert LATEST_URL == (
            f"s3://{TEST_BUCKET}/{WRM_STATIONS_ENHANCED_LATEST_S3_KEY}"
        )
        assert f"read_parquet('{LATEST_URL}')" in _view_ddl(
            ddl.statements, "wrm_stations_latest"
        )


# ---------------------------------------------------------------------------
# wrm_stations_latest_history: retained ranking view
# ---------------------------------------------------------------------------


class TestLatestHistoryViewDdl:
    def test_keeps_the_row_number_ranking_over_the_archive(self, ddl):
        ddl_sql = _view_ddl(ddl.statements, "wrm_stations_latest_history")

        assert (
            "ROW_NUMBER() OVER (PARTITION BY station_id ORDER BY date DESC, "
            "file_timestamp DESC)" in ddl_sql
        )
        assert "FROM wrm_stations_enhanced_data" in ddl_sql
        assert "WHERE record_type = 'station'" in ddl_sql
        assert "WHERE rn = 1" in ddl_sql

    def test_reads_the_archive_not_the_latest_snapshot(self, ddl):
        ddl_sql = _view_ddl(ddl.statements, "wrm_stations_latest_history")

        assert ARCHIVE_URL not in ddl_sql  # via the enhanced view, not directly
        assert LATEST_URL not in ddl_sql
        assert "read_parquet" not in ddl_sql

    def test_history_is_the_only_ranked_view(self, ddl):
        ranked = [
            view
            for view in _created_views(ddl.statements)
            if "ROW_NUMBER" in _view_ddl(ddl.statements, view).upper()
        ]

        assert ranked == ["wrm_stations_latest_history"]


# ---------------------------------------------------------------------------
# View namespace: nothing lost, nothing duplicated
# ---------------------------------------------------------------------------


class TestViewNamespace:
    def test_all_five_views_are_created_in_order(self, ddl):
        assert _created_views(ddl.statements) == EXPECTED_VIEWS

    def test_pre_existing_archive_views_are_unchanged(self, ddl):
        for view in ("wrm_stations_enhanced_data", "wrm_stations_only", "wrm_bikes_only"):
            assert any(
                f"CREATE OR REPLACE VIEW {view} AS" in statement
                for statement in ddl.statements
            )

    def test_sql_literal_escapes_single_quotes(self):
        assert views_module._sql_literal("a'b") == "a''b"

    def test_asset_identity_used_by_downstream_deps_is_unchanged(self):
        assert (
            create_duckdb_enhanced_views.op.tags["dagster/compute_kind"]
            == "duckdb"
        )
        assert {
            key.path[-1] for key in create_duckdb_enhanced_views.keys
        } == {"duckdb_enhanced_views"}

    def test_asset_depends_on_the_latest_enhanced_producer(self):
        """``wrm_stations_latest`` reads that asset's key, so the edge must exist.

        Without the dependency Dagster could materialize the views before the
        15-minute refresh overwrites ``enhanced/latest/...``, binding
        ``wrm_stations_latest`` to a missing or stale object.
        """
        expected = wrm_stations_enhanced_data_latest_asset.key

        assert create_duckdb_enhanced_views.dependency_keys == {expected}
        (dep_key,) = create_duckdb_enhanced_views.dependency_keys
        assert dep_key.path == ["wrm_stations_enhanced_data_latest"]

    def test_latest_dependency_does_not_introduce_a_cycle(self):
        """The producer must never depend back on the views asset."""
        producer_deps = wrm_stations_enhanced_data_latest_asset.dependency_keys

        assert create_duckdb_enhanced_views.keys.isdisjoint(producer_deps)


# ---------------------------------------------------------------------------
# Latest snapshot probe tolerance / archive fail-fast
# ---------------------------------------------------------------------------


class TestLatestSnapshotProbe:
    def test_missing_latest_snapshot_warns_but_still_creates_views(
        self, monkeypatch
    ):
        harness = Harness(
            monkeypatch, latest_probe_error=RuntimeError("NoSuchKey")
        )

        result = harness.run()

        assert "successfully" in result.lower()
        harness.context.log.warning.assert_called_once()
        warning = harness.context.log.warning.call_args.args[0]
        assert "not readable yet" in warning
        assert "NoSuchKey" in warning
        # The warning names the asset that produces the missing key, matching
        # the declared dependency asserted in TestViewNamespace.
        assert "wrm_stations_enhanced_data_latest" in warning
        # Both latest views are still emitted despite the failed probe.
        assert any(
            "CREATE OR REPLACE VIEW wrm_stations_latest_history AS" in statement
            for statement in harness.statements
        )
        assert any(
            "CREATE OR REPLACE VIEW wrm_stations_latest AS" in statement
            for statement in harness.statements
        )

    def test_archive_probe_failure_is_fatal_and_creates_no_views(self, monkeypatch):
        harness = Harness(monkeypatch)

        # Rebuild the connection with the archive probe failing.
        def failing_connect(path):
            class _FailingArchiveConnection(_RecordingConnection):
                def execute(self, sql, *args, **kwargs):
                    normalized = " ".join(sql.split())
                    self._statements.append(normalized)
                    if (
                        normalized.startswith("SELECT COUNT(*) FROM read_parquet(")
                        and ARCHIVE_URL in normalized
                    ):
                        raise RuntimeError("AccessDenied")
                    return super().execute(sql, *args, **kwargs)

            return _FailingArchiveConnection(
                None, harness.statements, harness._path_map, None
            )

        monkeypatch.setattr(
            views_module,
            "duckdb",
            type("FakeDuckDB", (), {"connect": staticmethod(failing_connect)}),
        )

        with pytest.raises(RuntimeError, match="AccessDenied"):
            harness.run()

        assert _created_views(harness.statements) == []


# ---------------------------------------------------------------------------
# Behaviour: wrm_stations_latest is a snapshot, wrm_stations_latest_history ranks
# ---------------------------------------------------------------------------


class TestLatestViewBehavior:
    def test_returns_only_snapshot_station_rows_ordered_by_station_id(self, behavior):
        rows = behavior.conn.execute(
            "SELECT station_id, name FROM wrm_stations_latest"
        ).fetchall()

        assert rows == [("001", "S1-snapshot"), ("003", "S3-snapshot-stale")]

    def test_snapshot_row_wins_even_when_older_than_archive(self, behavior):
        row = behavior.conn.execute(
            "SELECT date, name FROM wrm_stations_latest WHERE station_id = '003'"
        ).fetchone()

        assert row[0].date() == date(2024, 1, 14)
        assert row[1] == "S3-snapshot-stale"

    def test_station_missing_from_snapshot_is_absent_from_latest(self, behavior):
        ids = [
            row[0]
            for row in behavior.conn.execute(
                "SELECT station_id FROM wrm_stations_latest"
            ).fetchall()
        ]

        # 002 exists in the archive (and history) but only as a bike row in
        # the latest snapshot.
        assert "002" not in ids

    def test_bike_rows_are_excluded_from_latest(self, behavior):
        names = [
            row[0]
            for row in behavior.conn.execute(
                "SELECT name FROM wrm_stations_latest"
            ).fetchall()
        ]

        assert all("BIKE" not in name for name in names)
        assert behavior.conn.execute(
            "SELECT COUNT(*) FROM wrm_stations_latest"
        ).fetchone()[0] == 2

    def test_empty_station_projection_yields_empty_view(self, tmp_path, monkeypatch):
        harness = BehaviorHarness(tmp_path, monkeypatch)
        harness.seed(
            latest_rows=[
                ("002", "bike", "2024-01-17", "2024-01-17 12:00:00", "BIKE 2", 0)
            ]
        )
        harness.run()
        try:
            assert harness.conn.execute(
                "SELECT COUNT(*) FROM wrm_stations_latest"
            ).fetchone()[0] == 0
        finally:
            harness.close()


class TestLatestHistoryViewBehavior:
    def test_returns_one_newest_archive_row_per_station(self, behavior):
        rows = behavior.conn.execute(
            "SELECT station_id, name FROM wrm_stations_latest_history "
            "ORDER BY station_id"
        ).fetchall()

        assert rows == [
            ("001", "S1-archive-new"),
            ("002", "S2"),
            ("003", "S3-archive-new"),
        ]

    def test_deduplicates_per_station_and_excludes_bikes(self, behavior):
        count = behavior.conn.execute(
            "SELECT COUNT(*) FROM wrm_stations_latest_history"
        ).fetchone()[0]
        record_types = {
            row[0]
            for row in behavior.conn.execute(
                "SELECT record_type FROM wrm_stations_latest_history"
            ).fetchall()
        }

        assert count == 3
        assert record_types == {"station"}

    def test_file_timestamp_breaks_same_date_ties(self, behavior):
        row = behavior.conn.execute(
            "SELECT name, date, file_timestamp FROM wrm_stations_latest_history "
            "WHERE station_id = '003'"
        ).fetchone()

        # Both 003 station rows share 2024-01-16; the later file_timestamp wins.
        assert row[0] == "S3-archive-new"
        assert row[1].date() == date(2024, 1, 16)
        assert row[2].time().isoformat() == "08:30:00"

    def test_latest_and_history_are_distinct_for_the_same_station(self, behavior):
        latest = behavior.conn.execute(
            "SELECT name FROM wrm_stations_latest WHERE station_id = '003'"
        ).fetchone()[0]
        history = behavior.conn.execute(
            "SELECT name FROM wrm_stations_latest_history WHERE station_id = '003'"
        ).fetchone()[0]

        assert latest == "S3-snapshot-stale"
        assert history == "S3-archive-new"
        assert latest != history

    def test_all_five_views_coexist_after_materialization(self, behavior):
        tables = {
            row[0]
            for row in behavior.conn.execute("SHOW TABLES").fetchall()
        }

        assert set(EXPECTED_VIEWS) <= tables


# ---------------------------------------------------------------------------
# Downstream consumers still target wrm_stations_latest (never *_history)
# ---------------------------------------------------------------------------


class TestConsumersKeepTheStableViewContract:
    def test_dashboard_snapshot_sql_reads_wrm_stations_latest(self):
        from wrm_pipeline.assets import dashboard

        for sql in (dashboard._STATIONS_SNAPSHOT_SQL, dashboard._DENSITY_SNAPSHOT_SQL):
            assert re.search(r"\bwrm_stations_latest\b", sql), sql
            assert "wrm_stations_latest_history" not in sql, sql

    def test_duckdb_consumers_still_reference_wrm_stations_latest(self):
        import importlib

        for module_name in (
            "wrm_pipeline.assets.duckdb.bike_spatial_density_analysis",
            "wrm_pipeline.assets.duckdb.query_station_summary",
        ):
            module = importlib.import_module(module_name)
            source = inspect.getsource(module)
            assert re.search(r"\bwrm_stations_latest\b", source), module_name
            assert "wrm_stations_latest_history" not in source, module_name
