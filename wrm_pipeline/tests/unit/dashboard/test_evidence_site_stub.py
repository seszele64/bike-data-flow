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
        """`evidence build` must run with no datasources until S9.x wires wrm/."""
        assert config["plugins"] == {"components": {}, "datasources": {}}

    def test_stub_locks_exact_top_level_keys(self, config: dict):
        assert set(config) == {"project", "plugins"}

    def test_config_is_credential_free(self, config_text: str):
        assert SECRET_PATTERN.search(config_text) is None

    def test_config_has_no_local_or_absolute_paths(self, config_text: str):
        assert LOCAL_PATH_PATTERN.search(config_text) is None


class TestSiteIndexPage:
    """dashboard/pages/index.md: the single S6.2 stub page."""

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
        """S6.2 ships exactly one page; later steps must update this lock."""
        entries = sorted(os.listdir(PAGES_DIR))
        assert entries == ["index.md"]


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
