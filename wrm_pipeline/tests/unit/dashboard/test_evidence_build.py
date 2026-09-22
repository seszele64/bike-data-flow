"""Unit tests for the dashboard ``evidence_build`` asset (B4.4).

Contract under test (wrm_pipeline/wrm_pipeline/assets/dashboard.py:118-177):
- ``evidence_build``: compute_kind="npm", group="dashboard",
  deps=[evidence_data_snapshot]; runs ``npm run build`` (Evidence static
  export) with a credential-free env.
- ``DASHBOARD_DIR`` = $WRM_DASHBOARD_DIR or <repo>/dashboard;
  ``BUILD_DIR`` = $WRM_DASHBOARD_BUILD_DIR or <dashboard>/build.
- ``_build_env()`` strips HETZNER_*/S3_* (case-insensitive via .upper()),
  reads os.environ only, never hardcodes keys; env never logged.

Strategy: pure unit, no Node/network. subprocess.run is mocked; path
constants are monkeypatched per-test; env-override semantics verified via
importlib.reload with patched os.environ. Secrets use dummy placeholders
only (never real keys).
"""

from __future__ import annotations

import importlib
import inspect
import os
import subprocess
from types import SimpleNamespace

import pytest
from dagster import AssetKey, MaterializeResult, build_asset_context

import wrm_pipeline.assets.dashboard as dashboard_module
from wrm_pipeline.assets.dashboard import _build_env, evidence_build, evidence_data_snapshot

pytestmark = pytest.mark.evidence


# --------------------------------------------------------------------------- #
# Registration: compute_kind / group / deps
# --------------------------------------------------------------------------- #
class TestAssetRegistration:
    def test_asset_key_is_evidence_build(self):
        assert evidence_build.keys == {AssetKey("evidence_build")}

    def test_group_name_is_dashboard(self):
        assert evidence_build.group_names_by_key == {
            AssetKey("evidence_build"): "dashboard"
        }

    def test_compute_kind_is_npm(self):
        assert evidence_build.op.tags["dagster/compute_kind"] == "npm"

    def test_depends_on_evidence_data_snapshot(self):
        """Downstream of the credential-free snapshot, never raw ingest."""
        assert evidence_build.asset_deps == {
            AssetKey("evidence_build"): {AssetKey("evidence_data_snapshot")}
        }

    def test_snapshot_is_upstream_of_build(self):
        assert evidence_data_snapshot.keys == {AssetKey("evidence_data_snapshot")}
        dep = next(iter(evidence_build.asset_deps[AssetKey("evidence_build")]))
        assert dep == next(iter(evidence_data_snapshot.keys))

    def test_reexported_from_assets_package(self):
        """B4.4 wiring: assets.py must re-export evidence_build (KNOWN GAP)."""
        from wrm_pipeline.assets import assets as assets_module

        assert hasattr(assets_module, "evidence_build")
        assert "evidence_build" in assets_module.__all__
        from wrm_pipeline.assets import evidence_build as reexported

        assert reexported is evidence_build


# --------------------------------------------------------------------------- #
# Paths: defaults + env overrides
# --------------------------------------------------------------------------- #
def _repo_root() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(dashboard_module.__file__), "..", "..", "..")
    )


class TestBuildPaths:
    def test_default_dashboard_dir_derives_from_repo_root(self):
        assert dashboard_module.DASHBOARD_DIR == os.path.join(_repo_root(), "dashboard")

    def test_default_build_dir_is_dashboard_build(self):
        assert dashboard_module.BUILD_DIR == os.path.join(
            dashboard_module.DASHBOARD_DIR, "build"
        )

    def test_secret_prefixes_locked(self):
        assert dashboard_module._SECRET_ENV_PREFIXES == ("HETZNER_", "S3_")

    def _reload_with_env(self, monkeypatch, env: dict) -> object:
        for var in ("WRM_DASHBOARD_DIR", "WRM_DASHBOARD_BUILD_DIR"):
            monkeypatch.delenv(var, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return importlib.reload(dashboard_module)

    def test_dashboard_dir_env_override(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "custom-dash")
        mod = self._reload_with_env(monkeypatch, {"WRM_DASHBOARD_DIR": custom})
        try:
            assert mod.DASHBOARD_DIR == custom
            # BUILD_DIR falls through to <custom>/build when build var unset.
            assert mod.BUILD_DIR == os.path.join(custom, "build")
        finally:
            importlib.reload(dashboard_module)

    def test_build_dir_env_override(self, monkeypatch, tmp_path):
        custom_build = str(tmp_path / "out")
        mod = self._reload_with_env(
            monkeypatch, {"WRM_DASHBOARD_BUILD_DIR": custom_build}
        )
        try:
            assert mod.BUILD_DIR == custom_build
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
            assert mod.DASHBOARD_DIR == os.path.join(_repo_root(), "dashboard")
            assert mod.BUILD_DIR == os.path.join(mod.DASHBOARD_DIR, "build")
        finally:
            importlib.reload(dashboard_module)


# --------------------------------------------------------------------------- #
# _build_env: secret stripping
# --------------------------------------------------------------------------- #
class TestBuildEnvSecretStripping:
    def _env_with(self, monkeypatch, extra: dict) -> dict:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        for k, v in extra.items():
            monkeypatch.setenv(k, v)
        return _build_env()

    def test_strips_hetzner_and_s3_prefixes(self, monkeypatch):
        env = self._env_with(
            monkeypatch,
            {
                "HETZNER_ACCESS_KEY_ID": "dummy-id",
                "HETZNER_SECRET_ACCESS_KEY": "dummy-secret",
                "HETZNER_ENDPOINT_URL": "https://dummy.example:9000",
                "S3_ACCESS_KEY": "dummy",
                "S3_SECRET": "dummy",
                "KEEP_ME": "yes",
            },
        )
        for banned in (
            "HETZNER_ACCESS_KEY_ID",
            "HETZNER_SECRET_ACCESS_KEY",
            "HETZNER_ENDPOINT_URL",
            "S3_ACCESS_KEY",
            "S3_SECRET",
        ):
            assert banned not in env
        assert env["KEEP_ME"] == "yes"
        assert env["PATH"] == "/usr/bin:/bin"

    def test_stripping_is_case_insensitive(self, monkeypatch):
        env = self._env_with(
            monkeypatch,
            {
                "hetzner_token": "dummy",
                "hetzner_endpoint": "dummy",
                "s3_secret_key": "dummy",
                "S3_region": "dummy",
            },
        )
        assert "hetzner_token" not in env
        assert "hetzner_endpoint" not in env
        assert "s3_secret_key" not in env
        assert "S3_region" not in env

    def test_lowercase_s3_prefix_stripped(self, monkeypatch):
        env = self._env_with(monkeypatch, {"s3_foo": "dummy"})
        assert "s3_foo" not in env

    def test_substring_not_prefix_preserved(self, monkeypatch):
        """MYHETZNER_KEY / XS3_ must survive: startswith, not contains."""
        env = self._env_with(
            monkeypatch, {"MYHETZNER_KEY": "v", "XS3_": "v", "S3X": "v"}
        )
        assert env["MYHETZNER_KEY"] == "v"
        assert env["XS3_"] == "v"
        assert env["S3X"] == "v"

    def test_secret_in_value_with_safe_key_preserved(self, monkeypatch):
        """Only keys are filtered; values are never inspected."""
        env = self._env_with(
            monkeypatch, {"MY_APP_CONFIG": "HETZNER_SECRET=dummy"}
        )
        assert env["MY_APP_CONFIG"] == "HETZNER_SECRET=dummy"

    def test_empty_secret_values_still_stripped(self, monkeypatch):
        env = self._env_with(
            monkeypatch, {"HETZNER_EMPTY": "", "S3_EMPTY": ""}
        )
        assert "HETZNER_EMPTY" not in env
        assert "S3_EMPTY" not in env

    def test_unicode_keys_preserved(self, monkeypatch):
        env = self._env_with(monkeypatch, {"APP_ŚWIDNICKA_🚲": "v"})
        assert env["APP_ŚWIDNICKA_🚲"] == "v"

    def test_does_not_mutate_os_environ(self, monkeypatch):
        monkeypatch.setenv("HETZNER_DUMMY_PROBE", "dummy")
        before = dict(os.environ)
        _build_env()
        assert dict(os.environ) == before

    def test_reads_environ_only_no_hardcoded_secrets(self):
        """_build_env source must not embed credential literals."""
        src = inspect.getsource(_build_env)
        assert "os.environ" in src
        # Only the two prefix literals may appear; no key material.
        lowered = src.lower()
        assert "dummy" not in lowered
        assert "access_key_id" not in lowered or "hetzner" not in lowered.replace(
            "hetzner_", ""
        )
        module_src = inspect.getsource(dashboard_module)
        # No secret-value assignment anywhere in dashboard.py.
        assert "dummy-secret" not in module_src.lower()


# --------------------------------------------------------------------------- #
# Execution: mocked npm (no Node, no network)
# --------------------------------------------------------------------------- #
# Direct invocation of an @asset requires a real Dagster context
# (AssetsDefinition.__call__ validates BaseDirectExecutionContext), so log
# capture patches a real build_asset_context()'s log methods in place.
from unittest.mock import MagicMock  # noqa: E402


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


def _stage_dashboard(monkeypatch, tmp_path, *, with_node_modules=True):
    dash = tmp_path / "dashboard"
    dash.mkdir(exist_ok=True)
    (dash / "package.json").write_text('{"name": "bike-data-dashboard"}')
    if with_node_modules:
        (dash / "node_modules").mkdir(exist_ok=True)
    monkeypatch.setattr(dashboard_module, "DASHBOARD_DIR", str(dash))
    monkeypatch.setattr(dashboard_module, "BUILD_DIR", str(dash / "build"))
    return str(dash)


class TestBuildExecution:
    def test_success_runs_npm_build_with_stripped_env(self, monkeypatch, tmp_path):
        dash = _stage_dashboard(monkeypatch, tmp_path)
        monkeypatch.setenv("HETZNER_SECRET_ACCESS_KEY", "dummy-should-be-stripped")
        monkeypatch.setenv("S3_SECRET", "dummy-should-be-stripped")
        monkeypatch.setenv("WRM_SAFE_VAR", "safe-value")

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured.update(kwargs)
            return SimpleNamespace(returncode=0, stdout="build ok", stderr="")

        monkeypatch.setattr(dashboard_module.subprocess, "run", fake_run)

        ctx = _ctx_with_captured_logs()
        result = evidence_build(ctx)

        assert isinstance(result, MaterializeResult)
        assert captured["cmd"] == ["npm", "run", "build"]
        assert captured["cwd"] == dash
        assert captured["capture_output"] is True and captured["text"] is True
        child_env = captured["env"]
        assert "HETZNER_SECRET_ACCESS_KEY" not in child_env
        assert "S3_SECRET" not in child_env
        assert child_env.get("WRM_SAFE_VAR") == "safe-value"
        assert result.metadata["dashboard_dir"] == dash
        assert result.metadata["command"] == "npm run build"
        assert result.metadata["build_dir"] == os.path.join(dash, "build")

    def test_success_metadata_build_exists_flag(self, monkeypatch, tmp_path):
        dash = _stage_dashboard(monkeypatch, tmp_path)
        os.makedirs(os.path.join(dash, "build"), exist_ok=True)
        monkeypatch.setattr(
            dashboard_module.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        )
        result = evidence_build(_ctx_with_captured_logs())
        assert result.metadata["build_exists"] is True

    def test_success_without_build_dir_reports_false(self, monkeypatch, tmp_path):
        _stage_dashboard(monkeypatch, tmp_path)
        monkeypatch.setattr(
            dashboard_module.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        )
        result = evidence_build(_ctx_with_captured_logs())
        assert result.metadata["build_exists"] is False

    def test_logs_tail_only_and_never_env(self, monkeypatch, tmp_path):
        _stage_dashboard(monkeypatch, tmp_path)
        monkeypatch.setenv("HETZNER_PROBE_SECRET", "dummy-leak-probe-xyz")
        long_out = "x" * 5000 + "TAIL-MARKER"
        monkeypatch.setattr(
            dashboard_module.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=0, stdout=long_out, stderr=""
            ),
        )
        ctx = _ctx_with_captured_logs()
        evidence_build(ctx)
        logged = "\n".join(ctx.infos)  # type: ignore[attr-defined]
        assert "TAIL-MARKER" in logged
        assert len(logged) <= 2000 + 64  # tail slice, not the full 5000
        assert "dummy-leak-probe-xyz" not in logged
        assert "HETZNER_PROBE_SECRET" not in logged

    def test_missing_package_json_raises_before_subprocess(
        self, monkeypatch, tmp_path
    ):
        dash = tmp_path / "dashboard"
        dash.mkdir()
        (dash / "node_modules").mkdir()
        monkeypatch.setattr(dashboard_module, "DASHBOARD_DIR", str(dash))
        called = []
        monkeypatch.setattr(
            dashboard_module.subprocess,
            "run",
            lambda *a, **k: called.append(1) or SimpleNamespace(returncode=0),
        )
        with pytest.raises(FileNotFoundError, match="package.json"):
            evidence_build(build_asset_context())
        assert called == []

    def test_missing_node_modules_raises_with_npm_ci_hint(
        self, monkeypatch, tmp_path
    ):
        dash = tmp_path / "dashboard"
        dash.mkdir(exist_ok=True)
        (dash / "package.json").write_text("{}")
        monkeypatch.setattr(dashboard_module, "DASHBOARD_DIR", str(dash))
        with pytest.raises(FileNotFoundError, match="npm ci"):
            evidence_build(build_asset_context())

    def test_npm_not_on_path_raises(self, monkeypatch, tmp_path):
        _stage_dashboard(monkeypatch, tmp_path)

        def boom(*a, **k):
            raise FileNotFoundError("no npm")

        monkeypatch.setattr(dashboard_module.subprocess, "run", boom)
        with pytest.raises(FileNotFoundError, match="npm not found on PATH"):
            evidence_build(build_asset_context())

    def test_nonzero_exit_raises_and_logs_stderr_tail(
        self, monkeypatch, tmp_path
    ):
        _stage_dashboard(monkeypatch, tmp_path)
        monkeypatch.setattr(
            dashboard_module.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=1, stdout="out", stderr="e" * 5000 + "ERR-TAIL"
            ),
        )
        ctx = _ctx_with_captured_logs()
        with pytest.raises(RuntimeError, match="exit code 1"):
            evidence_build(ctx)
        assert "ERR-TAIL" in "\n".join(ctx.errors)  # type: ignore[attr-defined]

    def test_accepts_real_dagster_context(self, monkeypatch, tmp_path):
        _stage_dashboard(monkeypatch, tmp_path)
        monkeypatch.setattr(
            dashboard_module.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        result = evidence_build(build_asset_context())
        assert isinstance(result, MaterializeResult)

    def test_error_message_names_relocation_var(self, monkeypatch, tmp_path):
        dash = str(tmp_path / "nowhere")
        monkeypatch.setattr(dashboard_module, "DASHBOARD_DIR", dash)
        with pytest.raises(FileNotFoundError, match="WRM_DASHBOARD_DIR"):
            evidence_build(build_asset_context())


# --------------------------------------------------------------------------- #
# Static contract: source never shells anything but `npm run build`
# --------------------------------------------------------------------------- #
class TestBuildCommandContract:
    def test_source_invokes_only_npm_run_build(self):
        from pathlib import Path as _Path

        src = _Path(dashboard_module.__file__).read_text()
        assert "['npm', 'run', 'build']" in src
        assert "shell=True" not in src
        assert "capture_output=True" in src
        # env= seam must route through the stripping helper, never os.environ raw.
        assert "env=_build_env()" in src

    def test_dashboard_build_script_is_evidence_build(self):
        import json

        repo_root = _repo_root()
        with open(os.path.join(repo_root, "dashboard", "package.json")) as fh:
            pkg = json.load(fh)
        assert pkg["scripts"]["build"] == "evidence build"
