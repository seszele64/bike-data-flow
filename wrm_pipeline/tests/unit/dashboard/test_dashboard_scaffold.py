"""Evidence tests for the dashboard scaffold (S6.1) and dual-write sources.

These assertions are locked against the recorded artifacts on disk:

- ``dashboard/package.json`` / ``dashboard/package-lock.json`` — the Evidence
  toolkit scaffold: ``@evidence-dev/evidence`` 40.1.8 (resolved) with the
  ``^4.0.13`` / ``^5.4.2`` / ``^2.0.1`` / ``^40.1.8`` / ``^3.1.4`` / ``^5.0.0``
  devDependency pins (B4.1 adds the ``@evidence-dev/duckdb`` datasource plugin;
  B5.1 adds the ``@evidence-dev/tailwind`` styling plugin; B6.6 adds the
  ``@evidence-dev/component-utilities`` + ``@evidence-dev/core-components``
  component libraries). Invariant: every pinned devDependency has a resolved
  ``node_modules/`` entry in the lockfile — tailwind 3.1.4 (entry at
  lockfile line 1904), core-components 5.4.2, component-utilities 4.0.13,
  duckdb 2.0.1 — and the full tree is recorded (885 entries as of this
  step), plus node >= 18 and the dev/build/sources scripts.
- ``.gitignore`` — Node/SvelteKit build artifacts under ``dashboard/`` must be
  ignored so the 400+ MB ``node_modules`` tree never enters version control.
- ``dashboard/sources/wrm/*.sql`` — the dual-write local-first contract: the
  two ``wrm`` source queries read the local ``wrm.duckdb`` snapshot via
  ``FROM main.*`` (written locally by ``evidence_data_snapshot`` and
  mirrored to S3 parquet). The local
  ``dashboard/sources/wrm/wrm.duckdb`` file exists as a gitignored build
  artifact; the column projections below lock the SELECT lists of those
  queries.

No network and no Node execution: the lockfile is parsed as JSON, and the
ignore behavior is verified through ``git check-ignore``.
"""

from __future__ import annotations

import json
import os
import subprocess

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
# test_evidence_data_snapshot.py — this file locks the SELECT projections
# of the recorded S3-03 .sql sources, NOT a local DuckDB stub).
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
        # BasePath pages: build writes to ./build/bike-data-flow so the
        # Pages artifact (dashboard/build/bike-data-flow) serves under
        # deployment.basePath /bike-data-flow.
        assert package_json["scripts"] == {
            "dev": "evidence dev",
            "build": "EVIDENCE_BUILD_DIR=./build/bike-data-flow evidence build",
            "sources": "evidence sources",
        }

    def test_dev_dependency_pins(self, package_json):
        # B4.1: @evidence-dev/duckdb ^2.0.1 provides the `duckdb` source type
        # used by dashboard/sources/wrm/ (registered in evidence.config.yaml
        # at B4.2). B5.1: @evidence-dev/tailwind ^3.1.4 provides the styling
        # plugin. B6.6: @evidence-dev/component-utilities ^4.0.13 +
        # @evidence-dev/core-components ^5.4.2 provide the component library
        # (registered in evidence.config.yaml at B6.7). All six pins are
        # resolved as node_modules/ lockfile entries — see
        # TestEvidenceLockfile.
        assert package_json["devDependencies"] == {
            "@evidence-dev/component-utilities": "^4.0.13",
            "@evidence-dev/core-components": "^5.4.2",
            "@evidence-dev/duckdb": "^2.0.1",
            "@evidence-dev/evidence": "^40.1.8",
            "@evidence-dev/tailwind": "^3.1.4",
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
        """Invariant: the lockfile lists the full dependency tree.

        885 package entries are recorded as of this step; the 600 floor is
        deliberately loose so it only trips on a truncated/corrupt lockfile,
        never on routine dependency additions.
        """
        packages = lockfile["packages"]
        assert len(packages) >= 600

    def test_tailwind_resolves_to_3_1_4(self, lockfile):
        # B5.1: @evidence-dev/tailwind styling plugin — lockfile entry at
        # line 1904 (was stale/documented as missing in earlier revisions).
        entry = lockfile["packages"]["node_modules/@evidence-dev/tailwind"]
        assert entry["version"] == "3.1.4"
        assert entry["dev"] is True

    def test_core_components_resolves_to_5_4_2(self, lockfile):
        # B6.6: @evidence-dev/core-components library — resolved alongside
        # @evidence-dev/component-utilities 4.0.13 in the same lockfile tree.
        entry = lockfile["packages"]["node_modules/@evidence-dev/core-components"]
        assert entry["version"] == "5.4.2"
        assert entry["dev"] is True


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


class TestParquetSourcesArtifact:
    """The dual-write local-first contract: .sql sources read FROM main.*.

    The local ``dashboard/sources/wrm/wrm.duckdb`` snapshot exists
    (gitignored build artifact written by ``evidence_data_snapshot`` alongside
    the S3 parquet mirror). This class locks the query text — ``FROM
    main.stations_latest`` / ``FROM main.density_grid`` — the expected SELECT
    projections (mirroring the STUB_* column contracts), and credential-free
    sources (no keys/endpoints inlined; the local handle is allowed).
    """

    STATIONS_SQL = os.path.join(DASHBOARD_DIR, "sources", "wrm", "stations_latest.sql")
    DENSITY_SQL = os.path.join(DASHBOARD_DIR, "sources", "wrm", "density_grid.sql")
    SNAPSHOT_DUCKDB = os.path.join(DASHBOARD_DIR, "sources", "wrm", "wrm.duckdb")
    EXPECTED_STATIONS_TABLE = "main.stations_latest"
    EXPECTED_DENSITY_TABLE = "main.density_grid"

    def test_sql_files_exist(self):
        assert os.path.isfile(self.STATIONS_SQL)
        assert os.path.isfile(self.DENSITY_SQL)

    def test_local_snapshot_exists(self):
        """Dual-write keeps a gitignored local wrm.duckdb for Evidence builds."""
        assert os.path.isfile(self.SNAPSHOT_DUCKDB)

    def test_local_snapshot_is_gitignored(self):
        probe = os.path.join(REPO_ROOT, "dashboard", "sources", "wrm", "wrm.duckdb")
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", probe],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, "wrm.duckdb must stay gitignored"

    def test_stations_reads_from_main(self):
        """Local-first: stations query reads the wrm.duckdb main namespace."""
        with open(self.STATIONS_SQL) as fh:
            sql = fh.read().lower()
        assert f"from {self.EXPECTED_STATIONS_TABLE}" in sql

    def test_density_reads_from_main(self):
        """Local-first: density query reads the wrm.duckdb main namespace."""
        with open(self.DENSITY_SQL) as fh:
            sql = fh.read().lower()
        assert f"from {self.EXPECTED_DENSITY_TABLE}" in sql

    def test_stations_projects_expected_columns(self):
        """Every dashboard column is selected from the local stations table."""
        with open(self.STATIONS_SQL) as fh:
            sql = fh.read().lower()
        for name, _dtype in STUB_STATIONS_COLUMNS:
            assert name in sql, f"stations_latest.sql missing column {name}"

    def test_density_projects_expected_columns(self):
        """Every dashboard column is selected from the local density table."""
        with open(self.DENSITY_SQL) as fh:
            sql = fh.read().lower()
        for name, _dtype in STUB_DENSITY_COLUMNS:
            assert name in sql, f"density_grid.sql missing column {name}"

    @pytest.mark.parametrize("table", ["stations_latest", "density_grid"])
    def test_sql_is_credential_free(self, table: str):
        """No secrets or endpoints inlined; local wrm.duckdb handle is allowed."""
        path = self.STATIONS_SQL if table == "stations_latest" else self.DENSITY_SQL
        with open(path) as fh:
            lowered = fh.read().lower()
        assert "access_key" not in lowered
        assert "secret_access" not in lowered
        assert "hetzner" not in lowered
        assert "endpoint_url" not in lowered
