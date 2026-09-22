"""Evidence tests for the dashboard scaffold (S6.1) and snapshot stub (S9.1).

These assertions are locked against the recorded artifacts on disk:

- ``dashboard/package.json`` / ``dashboard/package-lock.json`` — the Evidence
  toolkit scaffold: ``@evidence-dev/evidence`` 40.1.8 (resolved) with the
  ``^2.0.1`` / ``^40.1.8`` / ``^5.0.0`` devDependency pins (B4.1 adds the
  ``@evidence-dev/duckdb`` datasource plugin; the lockfile still records the
  pre-B4.1 tree — no npm install in this offline step), node >= 18, and the
  dev/build/sources scripts.
- ``.gitignore`` — Node/SvelteKit build artifacts under ``dashboard/`` must be
  ignored so the 400+ MB ``node_modules`` tree never enters version control.
- ``dashboard/sources/wrm/wrm.duckdb`` — the S9.1 snapshot stub: a plain
  DuckDB file already shaped with the two ``wrm_dash`` tables (empty in the
  stub) that ``evidence_data_snapshot`` materializes.

No network and no Node execution: the lockfile is parsed as JSON, and the
ignore behavior is verified through ``git check-ignore``.
"""

from __future__ import annotations

import json
import os
import subprocess

import duckdb
import pytest

pytestmark = pytest.mark.evidence

REPO_ROOT = os.path.abspath(
    # tests/unit/dashboard/test_dashboard_scaffold.py → 4 levels up
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")

IGNORED_DASHBOARD_PATHS = (
    "dashboard/node_modules/",
    "dashboard/.svelte-kit/",
    "dashboard/.evidence/",
)

# The dashboard-facing schema contract (must stay in sync with
# assets/dashboard.py; also asserted at runtime by
# test_evidence_data_snapshot.py — this file locks the *recorded stub*).
STUB_STATIONS_COLUMNS = [
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
STUB_DENSITY_COLUMNS = [
    ("grid_lat", "DOUBLE"),
    ("grid_lon", "DOUBLE"),
    ("bike_count", "BIGINT"),
    ("station_count", "BIGINT"),
    ("density_per_1000m2", "BIGINT"),
]


class TestEvidenceScaffoldPackageJson:
    """dashboard/package.json pins the Evidence toolkit and scripts."""

    @pytest.fixture
    def package_json(self) -> dict:
        with open(os.path.join(DASHBOARD_DIR, "package.json")) as fh:
            return json.load(fh)

    def test_package_identity(self, package_json):
        assert package_json["name"] == "bike-data-dashboard"
        assert package_json["version"] == "0.0.1"
        assert package_json["private"] is True
        assert package_json["type"] == "module"

    def test_node_engine_floor(self, package_json):
        assert package_json["engines"]["node"] == ">=18"

    def test_evidence_scripts(self, package_json):
        assert package_json["scripts"] == {
            "dev": "evidence dev",
            "build": "evidence build",
            "sources": "evidence sources",
        }

    def test_dev_dependency_pins(self, package_json):
        # B4.1: @evidence-dev/duckdb ^2.0.1 provides the `duckdb` source type
        # used by dashboard/sources/wrm/ (registered in evidence.config.yaml
        # at B4.2). Lockfile intentionally still records the pre-B4.1 tree
        # (no npm install in this offline step) — see TestEvidenceLockfile.
        assert package_json["devDependencies"] == {
            "@evidence-dev/duckdb": "^2.0.1",
            "@evidence-dev/evidence": "^40.1.8",
            "typescript": "^5.0.0",
        }


class TestEvidenceLockfile:
    """package-lock.json resolves the pinned toolchain to the recorded versions."""

    @pytest.fixture
    def lockfile(self) -> dict:
        with open(os.path.join(DASHBOARD_DIR, "package-lock.json")) as fh:
            return json.load(fh)

    def test_lockfile_version_format(self, lockfile):
        assert lockfile["lockfileVersion"] >= 3
        assert lockfile["name"] == "bike-data-dashboard"

    def test_evidence_resolves_to_40_1_8(self, lockfile):
        entry = lockfile["packages"]["node_modules/@evidence-dev/evidence"]
        assert entry["version"] == "40.1.8"
        assert entry["dev"] is True

    def test_typescript_resolves_to_5_4_2(self, lockfile):
        entry = lockfile["packages"]["node_modules/typescript"]
        assert entry["version"] == "5.4.2"
        assert entry["dev"] is True

    def test_lockfile_covers_installed_tree(self, lockfile):
        """666 recorded packages: the lockfile lists the full dependency tree."""
        packages = lockfile["packages"]
        assert len(packages) >= 600


class TestEvidenceGitignore:
    """Node build artifacts are ignored so the scaffold stays out of git."""

    def test_gitignore_contains_dashboard_patterns(self):
        with open(os.path.join(REPO_ROOT, ".gitignore")) as fh:
            content = fh.read()
        for pattern in IGNORED_DASHBOARD_PATHS:
            assert pattern in content

    @pytest.mark.parametrize("artifact", IGNORED_DASHBOARD_PATHS)
    def test_git_check_ignore_matches(self, artifact: str):
        probe = os.path.join(REPO_ROOT, artifact, "probe")
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", probe],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, f"{artifact} is not git-ignored"

    def test_node_modules_never_tracked(self):
        """Belt and braces: nothing under dashboard/node_modules is in the index."""
        result = subprocess.run(
            ["git", "ls-files", "--", "dashboard/node_modules"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == ""


class TestSnapshotStubArtifact:
    """The recorded S9.1 stub: a credential-free DuckDB with the wrm_dash shape.

    Deliberately asserts existence/shape only — row counts change every
    scheduled materialization and are covered dynamically by
    test_evidence_data_snapshot.py.
    """

    @pytest.fixture
    def snapshot_path(self) -> str:
        return os.path.join(DASHBOARD_DIR, "sources", "wrm", "wrm.duckdb")

    def test_stub_exists_and_opens(self, snapshot_path):
        assert os.path.isfile(snapshot_path)
        conn = duckdb.connect(snapshot_path, read_only=True)
        conn.close()

    def test_stub_contains_both_dashboard_tables(self, snapshot_path):
        conn = duckdb.connect(snapshot_path, read_only=True)
        try:
            tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        finally:
            conn.close()
        assert tables == {"stations_latest", "density_grid"}

    def test_stub_stations_latest_schema(self, snapshot_path):
        conn = duckdb.connect(snapshot_path, read_only=True)
        try:
            described = [
                (r[0], r[1])
                for r in conn.execute("DESCRIBE stations_latest").fetchall()
            ]
        finally:
            conn.close()
        assert described == STUB_STATIONS_COLUMNS

    def test_stub_density_grid_schema(self, snapshot_path):
        conn = duckdb.connect(snapshot_path, read_only=True)
        try:
            described = [
                (r[0], r[1]) for r in conn.execute("DESCRIBE density_grid").fetchall()
            ]
        finally:
            conn.close()
        assert described == STUB_DENSITY_COLUMNS

    def test_stub_tables_are_selectable(self, snapshot_path):
        """Both tables respond to COUNT(*) (readable by the Evidence dashboard)."""
        conn = duckdb.connect(snapshot_path, read_only=True)
        try:
            for table in ("stations_latest", "density_grid"):
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert count >= 0
        finally:
            conn.close()
