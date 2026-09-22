"""Evidence tests for the S6.2 stub site: config + index page.

Locks the recorded artifacts added in commit ``7a52e37``:

- ``dashboard/evidence.config.yaml`` — minimal Evidence project config so
  ``evidence dev`` / ``evidence build`` run offline with empty data: project
  identity, empty ``plugins.components`` / ``plugins.datasources`` stubs (the
  real datasources land in S9.x under ``sources/``).
- ``dashboard/pages/index.md`` — the single stub page with Evidence-style
  YAML frontmatter (``title``) and a body that names the S9.1 snapshot tables
  (``stations_latest`` / ``density_grid``) so the copy stays consistent with
  the recorded DuckDB stub locked in ``test_dashboard_scaffold.py``.

B3 evolution (commit ``1418a7a``): the index page grew from stub copy into
the real stations page — `````sql stations`` / `````sql density`` queries
against ``wrm.stations_latest`` / ``wrm.density_grid`` plus ``DataTable`` /
``BarChart`` / ``LineChart`` components. The S6.2 locks that still hold are
kept verbatim (frontmatter ``title`` exact, body names both snapshot
tables, single page); the B3 query/table/chart contract is asserted by the
``TestIndexRealPageContract`` class below.

B4.2 evolution: ``evidence.config.yaml`` now registers the
``@evidence-dev/duckdb`` datasource plugin (``plugins.datasources`` is
``{"@evidence-dev/duckdb": {}}``); ``plugins.components`` stays ``{}``.

B6.7 evolution: ``plugins.components`` now registers the
``@evidence-dev/core-components`` component library
(``{"@evidence-dev/core-components": {}}``) used by dashboard pages
(incl. QueryViewer).

Both files must also be tracked in git — the dashboard is source-controlled
except for the ignored Node/SvelteKit build artifacts.

No network and no Node execution: the YAML frontmatter is parsed with PyYAML
(pulled in transitively via dagster), nothing shells out except
``git ls-files`` for the tracking assertions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
import yaml

pytestmark = pytest.mark.evidence

REPO_ROOT = os.path.abspath(
    # tests/unit/dashboard/test_evidence_site_stub.py → 4 levels up
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")
CONFIG_PATH = os.path.join(DASHBOARD_DIR, "evidence.config.yaml")
INDEX_PATH = os.path.join(DASHBOARD_DIR, "pages", "index.md")
PAGES_DIR = os.path.join(DASHBOARD_DIR, "pages")

PROJECT_NAME = "bike-data-dashboard"

S62_TRACKED_ARTIFACTS = (
    "dashboard/evidence.config.yaml",
    "dashboard/pages/index.md",
)

# A stub must stay credential-free (mirrors the credential-free S9.1 DuckDB
# stub): no credential-ish keys or inline secrets anywhere in the config.
SECRET_PATTERN = re.compile(
    r"\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credentials?)\b",
    re.IGNORECASE,
)

# No machine-local or absolute paths: the stub must be portable/offline.
LOCAL_PATH_PATTERN = re.compile(
    r"(/root/|/home/|/Users/|C:\\\\|file://)",
)


class TestEvidenceConfigStub:
    """dashboard/evidence.config.yaml: minimal, empty-data project config."""

    @pytest.fixture
    def config_text(self) -> str:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return fh.read()

    @pytest.fixture
    def config(self, config_text: str) -> dict:
        parsed = yaml.safe_load(config_text)
        assert isinstance(parsed, dict), "evidence.config.yaml must be a YAML mapping"
        return parsed

    def test_config_exists_and_parses_as_yaml(self, config: dict):
        assert os.path.isfile(CONFIG_PATH)
        assert config  # non-empty mapping (a bare comment-only file parses to None)

    def test_project_name_matches_scaffold(self, config: dict, package_name: str):
        assert config["project"] == {"name": PROJECT_NAME}
        # Consistency with the Evidence scaffold identity (S6.1).
        assert PROJECT_NAME == package_name

    def test_plugins_sections_are_empty_stubs(self, config: dict):
        """B4.2 + B6.7: components registers core-components; datasources duckdb."""
        assert config["plugins"] == {
            "components": {"@evidence-dev/core-components": {}},
            "datasources": {"@evidence-dev/duckdb": {}},
        }

    def test_stub_locks_exact_top_level_keys(self, config: dict):
        assert set(config) == {"project", "plugins"}

    def test_config_is_credential_free(self, config_text: str):
        assert SECRET_PATTERN.search(config_text) is None

    def test_config_has_no_local_or_absolute_paths(self, config_text: str):
        assert LOCAL_PATH_PATTERN.search(config_text) is None


class TestSiteIndexPage:
    """dashboard/pages/index.md: stub-era locks (kept) + B3 real-page base."""

    @pytest.fixture
    def index_text(self) -> str:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            return fh.read()

    def test_index_page_exists(self):
        assert os.path.isfile(INDEX_PATH)

    def test_index_is_utf8_without_bom(self, index_text: str):
        # open(..., encoding="utf-8") above already fails on invalid UTF-8;
        # a BOM would silently survive and could confuse the mdsvex frontmatter
        # parser, so reject it explicitly.
        assert not index_text.startswith("\ufeff")

    def test_frontmatter_declares_title(self, index_text: str):
        """Evidence pages require YAML frontmatter; the title is user-visible."""
        assert index_text.startswith("---\n")
        closing = index_text.find("\n---", 4)
        assert closing != -1, "frontmatter is never closed"
        frontmatter = yaml.safe_load(index_text[4:closing])
        assert frontmatter == {"title": "Bike Data Flow Dashboard"}

    def test_body_has_h1_heading(self, index_text: str):
        body = index_text.split("---", 2)[2]
        assert "# Bike Data Flow Dashboard" in body

    def test_body_references_snapshot_tables(self, index_text: str):
        """Page copy must name the S9.1 stub tables it will eventually chart."""
        body = index_text.split("---", 2)[2]
        assert "stations_latest" in body
        assert "density_grid" in body

    def test_index_is_only_page_at_stub_stage(self):
        """B3 keeps exactly one page (index.md); later steps update this lock."""
        entries = sorted(os.listdir(PAGES_DIR))
        assert entries == ["index.md"]


# The B3 real-page column contract mirrors the snapshot schema locked in
# wrm_pipeline/assets/dashboard.py (_STATIONS_SNAPSHOT_SQL 9 cols /
# _DENSITY_SNAPSHOT_SQL 5 cols) and asserted at runtime by
# test_evidence_data_snapshot.py — this class locks that the *page queries*
# project exactly those columns.
B3_STATIONS_COLUMNS = [
    "station_id",
    "name",
    "bikes",
    "spaces",
    "total_docks",
    "installed",
    "lat",
    "lon",
    "timestamp",
]

B3_DENSITY_COLUMNS = [
    "grid_lat",
    "grid_lon",
    "bike_count",
    "station_count",
    "density_per_1000m2",
]


def _sql_block(index_text: str, name: str) -> str:
    """Return the body of the ```sql <name> fenced block (raises if absent)."""
    pattern = re.compile(r"```sql\s+" + re.escape(name) + r"\b(.*?)(```)", re.DOTALL)
    match = pattern.search(index_text)
    assert match is not None, f"missing ```sql {name} block"
    return match.group(1)


class TestIndexRealPageContract:
    """dashboard/pages/index.md: the B3 real stations page (commit 1418a7a).

    Pure-text assertions (no Node / no live ``evidence build``): the page
    must expose the two snapshot queries under the ``wrm.<table>`` source
    qualifier with the exact snapshot-schema projections, and every
    ``DataTable`` / chart component must bind to a defined query name.
    """

    @pytest.fixture
    def index_text(self) -> str:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            return fh.read()

    # -- query blocks ------------------------------------------------------
    def test_stations_query_block_exists(self, index_text: str):
        assert "```sql stations" in index_text

    def test_density_query_block_exists(self, index_text: str):
        assert "```sql density" in index_text

    def test_exactly_two_sql_blocks(self, index_text: str):
        assert len(re.findall(r"```sql\s+\w+", index_text)) == 2

    def test_stations_query_reads_wrm_snapshot_table(self, index_text: str):
        block = _sql_block(index_text, "stations")
        assert "wrm.stations_latest" in block

    def test_density_query_reads_wrm_snapshot_table(self, index_text: str):
        block = _sql_block(index_text, "density")
        assert "wrm.density_grid" in block

    def test_stations_query_projects_full_snapshot_schema(self, index_text: str):
        """The 9 stations_latest cols (dashboard.py:35-39) must be selected."""
        block = _sql_block(index_text, "stations")
        lowered = block.lower()
        for column in B3_STATIONS_COLUMNS:
            assert re.search(r"\b" + re.escape(column) + r"\b", lowered), (
                f"stations query missing column {column!r}"
            )
        assert "select" in lowered
        assert "from" in lowered

    def test_density_query_projects_full_snapshot_schema(self, index_text: str):
        """The 5 density_grid cols (dashboard.py:43-69) must be selected."""
        block = _sql_block(index_text, "density")
        lowered = block.lower()
        for column in B3_DENSITY_COLUMNS:
            assert re.search(r"\b" + re.escape(column) + r"\b", lowered), (
                f"density query missing column {column!r}"
            )
        assert "select" in lowered
        assert "from" in lowered

    def test_queries_have_deterministic_order_by(self, index_text: str):
        assert "ORDER BY" in _sql_block(index_text, "stations")
        assert "ORDER BY" in _sql_block(index_text, "density")

    # -- components ----------------------------------------------------------
    def test_datatable_bound_to_each_query(self, index_text: str):
        assert "<DataTable data={stations}" in index_text
        assert "<DataTable data={density}" in index_text

    def test_stations_charts(self, index_text: str):
        """BarChart (bikes per station) + LineChart (bikes over time)."""
        assert re.search(r'<BarChart\s+data=\{stations\}[^>]*x="name"[^>]*y="bikes"', index_text)
        assert re.search(
            r'<LineChart\s+data=\{stations\}[^>]*x="timestamp"[^>]*y="bikes"', index_text
        )

    def test_density_chart(self, index_text: str):
        assert re.search(
            r'<BarChart\s+data=\{density\}[^>]*x="grid_lat"[^>]*y="density_per_1000m2"',
            index_text,
        )

    def test_component_data_refs_match_defined_queries(self, index_text: str):
        """Every data={name} must refer to a ```sql <name> block (no dangling)."""
        defined = set(re.findall(r"```sql\s+(\w+)", index_text))
        used = set(re.findall(r"data=\{(\w+)\}", index_text))
        assert used, "expected at least one data={...} component binding"
        assert used <= defined, f"dangling component refs: {used - defined}"

    # -- page structure ------------------------------------------------------
    def test_section_headings(self, index_text: str):
        body = index_text.split("---", 2)[2]
        assert "## Stations" in body
        assert "## Spatial density" in body

    def test_empty_state_names_snapshot_tables(self, index_text: str):
        """B3 keeps the 0-row empty-state note naming both snapshot tables."""
        body = index_text.split("---", 2)[2]
        assert "No data yet" in body
        assert "stations_latest" in body
        assert "density_grid" in body

    def test_page_is_credential_free(self, index_text: str):
        assert SECRET_PATTERN.search(index_text) is None

    def test_page_has_no_local_or_absolute_paths(self, index_text: str):
        assert LOCAL_PATH_PATTERN.search(index_text) is None


class TestS62ArtifactsTracked:
    """The S6.2 artifacts exist on disk AND are version-controlled."""

    @pytest.mark.parametrize("rel_path", S62_TRACKED_ARTIFACTS)
    def test_artifact_exists_on_disk(self, rel_path: str):
        assert os.path.isfile(os.path.join(REPO_ROOT, rel_path))

    @pytest.mark.parametrize("rel_path", S62_TRACKED_ARTIFACTS)
    def test_artifact_is_git_tracked(self, rel_path: str):
        result = subprocess.run(
            ["git", "ls-files", "--", rel_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == rel_path


@pytest.fixture
def package_name() -> str:
    """Name from dashboard/package.json, for the config/scaffold identity check."""
    with open(os.path.join(DASHBOARD_DIR, "package.json"), encoding="utf-8") as fh:
        return json.load(fh)["name"]
