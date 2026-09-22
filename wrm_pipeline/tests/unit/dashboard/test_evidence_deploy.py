"""Unit tests for the dashboard ``evidence_deploy`` asset + Pages workflow (B5).

Contract under test (wrm_pipeline/wrm_pipeline/assets/dashboard.py:197-247):
- ``evidence_deploy``: compute_kind="static", group="dashboard",
  deps=[evidence_build]; verifies the credential-free static build output
  (``BUILD_DIR`` == $WRM_DASHBOARD_BUILD_DIR or <dashboard>/build) exists
  and is non-empty, counts files via ``os.walk``, returns metadata
  {build_dir, file_count, upload="pages-workflow"}. No upload happens here
  (the GitHub Pages workflow owns publish); the deploy context reuses the
  ``_build_env`` secret-free contract so a future uploader cannot receive
  HETZNER_*/S3_* credentials.
- ``.github/workflows/pages.yml`` (48 lines, B5.5): build job
  (checkout/setup-node/npm ci/sources/build/upload-pages-artifact) +
  deploy job (deploy-pages), permissions contents:read/pages:write/
  id-token:write, concurrency group pages.
- ``.gitignore`` (B5.2): ``dashboard/build`` ignored so the static export
  never enters version control.

Strategy: pure unit, no Node/network. BUILD_DIR is monkeypatched per-test;
env-override semantics verified via importlib.reload with patched
os.environ. Workflow assertions parse pages.yml as YAML plus raw-text pins.
Secrets use dummy placeholders only (never real keys).
"""

from __future__ import annotations

import importlib
import os
import subprocess

import pytest
import yaml
from dagster import AssetKey, MaterializeResult, build_asset_context

import wrm_pipeline.assets.dashboard as dashboard_module
from wrm_pipeline.assets.dashboard import evidence_build, evidence_deploy

pytestmark = pytest.mark.evidence

REPO_ROOT = os.path.abspath(
    # tests/unit/dashboard/test_evidence_deploy.py -> 4 levels up
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "pages.yml")
GITIGNORE_PATH = os.path.join(REPO_ROOT, ".gitignore")


def _repo_root() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(dashboard_module.__file__), "..", "..", "..")
    )


def _ctx_with_captured_logs():
    from dagster import build_asset_context as _build_ctx

    ctx = _build_ctx()
    infos: list[str] = []
    errors: list[str] = []
    ctx.log.info = infos.append  # type: ignore[method-assign]
    ctx.log.error = errors.append  # type: ignore[method-assign]
    ctx.infos = infos  # type: ignore[attr-defined]
    ctx.errors = errors  # type: ignore[attr-defined]
    return ctx


def _stage_build(monkeypatch, tmp_path, files: dict[str, str] | None = None) -> str:
    """Create a fake BUILD_DIR with the given relative files; patch module."""
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)
    for rel, content in (files or {"index.html": "<html></html>"}).items():
        target = build / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr(dashboard_module, "BUILD_DIR", str(build))
    return str(build)


def _load_workflow() -> dict:
    with open(WORKFLOW_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "pages.yml must parse to a mapping"
    return data


def _workflow_on(data: dict) -> dict:
    # YAML 1.1 parses bare `on` as boolean True; accept either key.
    on = data.get("on", data.get(True))
    assert isinstance(on, dict), "`on` must be a mapping"
    return on


# --------------------------------------------------------------------------- #
# Registration: compute_kind / group / deps
# --------------------------------------------------------------------------- #
class TestDeployAssetRegistration:
    def test_asset_key_is_evidence_deploy(self):
        assert evidence_deploy.keys == {AssetKey("evidence_deploy")}

    def test_group_name_is_dashboard(self):
        assert evidence_deploy.group_names_by_key == {
            AssetKey("evidence_deploy"): "dashboard"
        }

    def test_compute_kind_is_static(self):
        assert evidence_deploy.op.tags["dagster/compute_kind"] == "static"

    def test_depends_on_evidence_build(self):
        """Directly downstream of the npm static export, never the snapshot."""
        assert evidence_deploy.asset_deps == {
            AssetKey("evidence_deploy"): {AssetKey("evidence_build")}
        }

    def test_build_is_upstream_of_deploy(self):
        assert evidence_build.keys == {AssetKey("evidence_build")}
        dep = next(iter(evidence_deploy.asset_deps[AssetKey("evidence_deploy")]))
        assert dep == next(iter(evidence_build.keys))

    def test_deploy_is_terminal_of_dashboard_chain(self):
        """snapshot -> build -> deploy ordering is explicit in deps."""
        assert evidence_build.asset_deps == {
            AssetKey("evidence_build"): {AssetKey("evidence_data_snapshot")}
        }
        deploy_dep = next(
            iter(evidence_deploy.asset_deps[AssetKey("evidence_deploy")])
        )
        build_dep = next(
            iter(evidence_build.asset_deps[AssetKey("evidence_build")])
        )
        assert deploy_dep == AssetKey("evidence_build")
        assert build_dep == AssetKey("evidence_data_snapshot")

    def test_reexported_from_assets_package(self):
        """B5.4 wiring: assets.py must re-export evidence_deploy."""
        from wrm_pipeline.assets import assets as assets_module

        assert hasattr(assets_module, "evidence_deploy")
        assert "evidence_deploy" in assets_module.__all__
        from wrm_pipeline.assets import evidence_deploy as reexported

        assert reexported is evidence_deploy


# --------------------------------------------------------------------------- #
# Paths: defaults + env overrides
# --------------------------------------------------------------------------- #
class TestDeployPaths:
    def test_default_build_dir_is_dashboard_build(self):
        assert dashboard_module.BUILD_DIR == os.path.join(
            dashboard_module.DASHBOARD_DIR, "build"
        )

    def test_default_derives_from_repo_root(self):
        assert dashboard_module.DASHBOARD_DIR == os.path.join(_repo_root(), "dashboard")
        assert dashboard_module.BUILD_DIR == os.path.join(
            _repo_root(), "dashboard", "build"
        )

    def _reload_with_env(self, monkeypatch, env: dict) -> object:
        for var in ("WRM_DASHBOARD_DIR", "WRM_DASHBOARD_BUILD_DIR"):
            monkeypatch.delenv(var, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return importlib.reload(dashboard_module)

    def test_build_dir_env_override(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "out")
        mod = self._reload_with_env(
            monkeypatch, {"WRM_DASHBOARD_BUILD_DIR": custom}
        )
        try:
            assert mod.BUILD_DIR == custom
        finally:
            importlib.reload(dashboard_module)

    def test_dashboard_dir_override_cascades_to_build(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "custom-dash")
        mod = self._reload_with_env(monkeypatch, {"WRM_DASHBOARD_DIR": custom})
        try:
            assert mod.DASHBOARD_DIR == custom
            assert mod.BUILD_DIR == os.path.join(custom, "build")
        finally:
            importlib.reload(dashboard_module)

    def test_both_overrides_independent(self, monkeypatch, tmp_path):
        dash = str(tmp_path / "d")
        out = str(tmp_path / "o")
        mod = self._reload_with_env(
            monkeypatch,
            {"WRM_DASHBOARD_DIR": dash, "WRM_DASHBOARD_BUILD_DIR": out},
        )
        try:
            assert mod.DASHBOARD_DIR == dash
            assert mod.BUILD_DIR == out
        finally:
            importlib.reload(dashboard_module)

    def test_empty_string_env_falls_back_to_default(self, monkeypatch):
        mod = self._reload_with_env(
            monkeypatch,
            {"WRM_DASHBOARD_DIR": "", "WRM_DASHBOARD_BUILD_DIR": ""},
        )
        try:
            assert mod.BUILD_DIR == os.path.join(mod.DASHBOARD_DIR, "build")
            assert mod.DASHBOARD_DIR == os.path.join(_repo_root(), "dashboard")
        finally:
            importlib.reload(dashboard_module)


# --------------------------------------------------------------------------- #
# Execution: file counting + failure paths (no Node, no network)
# --------------------------------------------------------------------------- #
class TestDeployExecution:
    def test_success_single_file_metadata(self, monkeypatch, tmp_path):
        build = _stage_build(monkeypatch, tmp_path, {"index.html": "<h1>x</h1>"})
        result = evidence_deploy(_ctx_with_captured_logs())
        assert isinstance(result, MaterializeResult)
        assert result.metadata["build_dir"] == build
        assert result.metadata["file_count"] == 1
        assert result.metadata["upload"] == "pages-workflow"

    def test_success_counts_nested_files(self, monkeypatch, tmp_path):
        build = _stage_build(
            monkeypatch,
            tmp_path,
            {
                "index.html": "a",
                "_app/immutable/chunks/a.js": "js",
                "_app/immutable/chunks/b.js": "js",
                "data/wrm/stations.json": "{}",
            },
        )
        result = evidence_deploy(build_asset_context())
        assert result.metadata["file_count"] == 4
        assert result.metadata["build_dir"] == build

    def test_directories_are_not_counted(self, monkeypatch, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "empty-sub").mkdir()
        (build_dir / "index.html").write_text("x")
        monkeypatch.setattr(dashboard_module, "BUILD_DIR", str(build_dir))
        result = evidence_deploy(build_asset_context())
        assert result.metadata["file_count"] == 1

    def test_hidden_and_unicode_filenames_counted(self, monkeypatch, tmp_path):
        build = _stage_build(
            monkeypatch,
            tmp_path,
            {
                ".nojekyll": "",
                "Świdnicka–_🚲.html": "unicode",
                "spaced name.html": "spaces",
            },
        )
        result = evidence_deploy(build_asset_context())
        assert result.metadata["file_count"] == 3
        assert result.metadata["build_dir"] == build

    def test_many_files_counted(self, monkeypatch, tmp_path):
        files = {f"page-{i:03d}.html": "x" for i in range(50)}
        _stage_build(monkeypatch, tmp_path, files)
        result = evidence_deploy(build_asset_context())
        assert result.metadata["file_count"] == 50

    def test_missing_build_dir_raises_with_relocation_hint(
        self, monkeypatch, tmp_path
    ):
        missing = str(tmp_path / "no-build")
        monkeypatch.setattr(dashboard_module, "BUILD_DIR", missing)
        with pytest.raises(FileNotFoundError, match="WRM_DASHBOARD_BUILD_DIR"):
            evidence_deploy(build_asset_context())

    def test_missing_build_error_names_path(self, monkeypatch, tmp_path):
        missing = str(tmp_path / "absent")
        monkeypatch.setattr(dashboard_module, "BUILD_DIR", missing)
        with pytest.raises(FileNotFoundError) as excinfo:
            evidence_deploy(build_asset_context())
        assert missing in str(excinfo.value)

    def test_build_path_is_file_not_dir_raises(self, monkeypatch, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        monkeypatch.setattr(dashboard_module, "BUILD_DIR", str(blocker))
        with pytest.raises(FileNotFoundError):
            evidence_deploy(build_asset_context())

    def test_empty_build_dir_raises(self, monkeypatch, tmp_path):
        empty = tmp_path / "build"
        empty.mkdir()
        monkeypatch.setattr(dashboard_module, "BUILD_DIR", str(empty))
        with pytest.raises(RuntimeError, match="empty"):
            evidence_deploy(build_asset_context())

    def test_only_subdirs_no_files_raises(self, monkeypatch, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "sub").mkdir()
        (build_dir / "sub" / "nested").mkdir()
        monkeypatch.setattr(dashboard_module, "BUILD_DIR", str(build_dir))
        with pytest.raises(RuntimeError, match="empty"):
            evidence_deploy(build_asset_context())

    def test_logs_file_count_and_pages_handoff(self, monkeypatch, tmp_path):
        _stage_build(monkeypatch, tmp_path, {"a.html": "x", "b.html": "y"})
        ctx = _ctx_with_captured_logs()
        evidence_deploy(ctx)
        logged = "\n".join(ctx.infos)  # type: ignore[attr-defined]
        assert "2 files" in logged
        assert "Pages workflow" in logged

    def test_secrets_in_env_do_not_break_deploy(self, monkeypatch, tmp_path):
        _stage_build(monkeypatch, tmp_path)
        monkeypatch.setenv("HETZNER_SECRET_ACCESS_KEY", "dummy-should-be-filtered")
        monkeypatch.setenv("S3_SECRET", "dummy-should-be-filtered")
        result = evidence_deploy(_ctx_with_captured_logs())
        assert result.metadata["file_count"] == 1

    def test_secrets_never_logged(self, monkeypatch, tmp_path):
        _stage_build(monkeypatch, tmp_path)
        monkeypatch.setenv("HETZNER_PROBE_SECRET", "dummy-leak-probe-xyz")
        ctx = _ctx_with_captured_logs()
        evidence_deploy(ctx)
        logged = "\n".join(ctx.infos)  # type: ignore[attr-defined]
        assert "dummy-leak-probe-xyz" not in logged

    def test_accepts_real_dagster_context(self, monkeypatch, tmp_path):
        _stage_build(monkeypatch, tmp_path)
        result = evidence_deploy(build_asset_context())
        assert isinstance(result, MaterializeResult)


# --------------------------------------------------------------------------- #
# Secret-free + upload-free static contract
# --------------------------------------------------------------------------- #
class TestDeployStaticContract:
    def test_source_uses_build_env_filter(self):
        from pathlib import Path as _Path

        src = _Path(dashboard_module.__file__).read_text()
        deploy_src = src.split("# --- evidence_deploy", 1)[1]
        assert "env = _build_env()" in deploy_src
        assert "os.walk(BUILD_DIR)" in deploy_src or "os.walk( BUILD_DIR" in deploy_src

    def test_source_records_pages_workflow_handoff(self):
        from pathlib import Path as _Path

        src = _Path(dashboard_module.__file__).read_text()
        assert '"upload": "pages-workflow"' in src

    def test_source_performs_no_upload(self):
        from pathlib import Path as _Path

        src = _Path(dashboard_module.__file__).read_text()
        deploy_src = src.split("# --- evidence_deploy", 1)[1]
        lowered = deploy_src.lower()
        assert "boto3" not in lowered
        assert "upload_file" not in lowered
        assert "put_object" not in lowered
        assert "subprocess" not in deploy_src
        assert "shell=True" not in deploy_src

    def test_source_never_logs_secrets(self):
        from pathlib import Path as _Path

        src = _Path(dashboard_module.__file__).read_text()
        deploy_src = src.split("# --- evidence_deploy", 1)[1]
        assert "never reach" in deploy_src or "never logged" in deploy_src
        assert "HETZNER_SECRET_ACCESS_KEY" not in deploy_src
        assert "dummy" not in deploy_src.lower()

    def test_metadata_keys_locked(self, monkeypatch, tmp_path):
        _stage_build(monkeypatch, tmp_path, {"index.html": "x"})
        result = evidence_deploy(build_asset_context())
        assert set(result.metadata) == {"build_dir", "file_count", "upload"}


# --------------------------------------------------------------------------- #
# Pages workflow (B5.5): jobs + permissions
# --------------------------------------------------------------------------- #
class TestPagesWorkflow:
    def test_workflow_file_exists(self):
        assert os.path.isfile(WORKFLOW_PATH)

    def test_workflow_name_mentions_pages(self):
        assert "Pages" in _load_workflow()["name"]

    def test_triggers_push_branches_and_dispatch(self):
        on = _workflow_on(_load_workflow())
        assert set(on["push"]["branches"]) == {"agent/dashboard", "main"}
        assert "workflow_dispatch" in on

    def test_permissions_locked(self):
        perms = _load_workflow()["permissions"]
        assert perms == {"contents": "read", "pages": "write", "id-token": "write"}

    def test_concurrency_group_pages_no_cancel(self):
        conc = _load_workflow()["concurrency"]
        assert conc == {"group": "pages", "cancel-in-progress": False}

    def test_jobs_are_build_then_deploy(self):
        jobs = _load_workflow()["jobs"]
        assert set(jobs) == {"build", "deploy"}
        assert jobs["deploy"]["needs"] == "build"

    def test_build_runs_on_ubuntu(self):
        assert _load_workflow()["jobs"]["build"]["runs-on"] == "ubuntu-latest"

    def test_build_steps_pipeline(self):
        steps = _load_workflow()["jobs"]["build"]["steps"]
        uses = [s.get("uses", "") for s in steps]
        runs = [s.get("run", "") for s in steps]
        assert "actions/checkout@v4" in uses
        assert "actions/setup-node@v4" in uses
        assert "actions/upload-pages-artifact@v3" in uses
        assert "npm ci" in runs
        assert "npm run sources" in runs
        assert "npm run build" in runs

    def test_build_node_version_pinned_20(self):
        steps = _load_workflow()["jobs"]["build"]["steps"]
        setup = next(s for s in steps if s.get("uses", "").startswith("actions/setup-node"))
        assert setup["with"]["node-version"] == "20"

    def test_build_npm_steps_run_in_dashboard_dir(self):
        steps = _load_workflow()["jobs"]["build"]["steps"]
        npm_steps = [s for s in steps if s.get("run", "").startswith("npm")]
        assert len(npm_steps) == 3
        assert all(s.get("working-directory") == "dashboard" for s in npm_steps)

    def test_upload_artifact_path_is_dashboard_build(self):
        steps = _load_workflow()["jobs"]["build"]["steps"]
        upload = next(s for s in steps if "upload-pages-artifact" in s.get("uses", ""))
        assert upload["with"]["path"] == "dashboard/build"

    def test_deploy_job_environment_and_action(self):
        deploy = _load_workflow()["jobs"]["deploy"]
        assert deploy["runs-on"] == "ubuntu-latest"
        assert deploy["environment"]["name"] == "github-pages"
        assert "page_url" in deploy["environment"]["url"]
        uses = [s.get("uses", "") for s in deploy["steps"]]
        assert "actions/deploy-pages@v4" in uses

    def test_workflow_is_credential_free(self):
        with open(WORKFLOW_PATH, encoding="utf-8") as fh:
            text = fh.read()
        lowered = text.lower()
        assert "hetzner" not in lowered
        assert "s3_secret" not in lowered
        assert "password" not in lowered
        assert "api_key" not in lowered

    def test_workflow_is_git_tracked(self):
        result = subprocess.run(
            ["git", "ls-files", "--", ".github/workflows/pages.yml"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == ".github/workflows/pages.yml"


# --------------------------------------------------------------------------- #
# .gitignore (B5.2): static build output stays out of git
# --------------------------------------------------------------------------- #
class TestBuildIgnore:
    def test_gitignore_contains_build_pattern(self):
        with open(GITIGNORE_PATH, encoding="utf-8") as fh:
            content = fh.read()
        assert "dashboard/build" in content

    def test_git_check_ignore_matches_build(self):
        probe = os.path.join(REPO_ROOT, "dashboard", "build", "probe")
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", probe],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, "dashboard/build is not git-ignored"

    def test_build_output_never_tracked(self):
        result = subprocess.run(
            ["git", "ls-files", "--", "dashboard/build"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == ""
