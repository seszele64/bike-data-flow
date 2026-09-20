"""S4 regression tests: unified DuckDB path with WRM_DUCKDB_PATH override.

Verifies the S4 contract for ``wrm_pipeline.wrm_pipeline.config.db_path`` and
its consumers in ``resources.py``:

* default ``db_path`` is the repo-root ``db/analytics.duckdb`` (never a
  ``~/data`` location),
* the parent directory of a local path is created on import (``makedirs``),
* the ``WRM_DUCKDB_PATH`` environment variable overrides the default,
* remote URIs (``s3://``, ``gs://``, ``https://``) skip directory creation,
* the local DuckDB IO managers in ``resources.py`` are wired to
  ``config.db_path`` while the S3 DuckDB manager keeps its ``s3://`` URI.

``db_path`` is computed at import time, so override tests reload the config
module inside :func:`_reloaded_config`, which restores both ``os.environ``
and the module state afterwards so no other test is affected.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import wrm_pipeline.wrm_pipeline.config as config
import wrm_pipeline.wrm_pipeline.resources as resources

DB_DIRNAME = "db"
DB_FILENAME = "analytics.duckdb"
DB_SUFFIX = f"{DB_DIRNAME}/{DB_FILENAME}"
ENV_VAR = "WRM_DUCKDB_PATH"

# Captured at import time so skip conditions stay stable across module reloads.
IO_MANAGER_AT_IMPORT = resources.duckdb_io_manager
HYBRID_IO_MANAGER_AT_IMPORT = resources.duckdb_hybrid_io_manager
S3_IO_MANAGER_AT_IMPORT = resources.duckdb_s3_io_manager
DUCKDB_PANDAS_INSTALLED = importlib.util.find_spec("dagster_duckdb_pandas") is not None

NO_MANAGER_REASON = (
    "dagster-duckdb-pandas not installed; duckdb IO manager is None "
    "(optional integration degraded gracefully)"
)


@contextmanager
def _reloaded_config(env: dict[str, str] | None = None, unset: tuple[str, ...] = ()):
    """Reload ``config`` under a controlled ``WRM_DUCKDB_PATH`` environment.

    ``db_path`` (and its ``makedirs`` side effect) run at import time, so the
    module must be re-executed to observe environment changes. The context
    manager restores the original environment value and reloads the module on
    exit so surrounding tests keep seeing the ambient configuration.
    """
    saved = {name: os.environ.get(name) for name in (ENV_VAR, *unset)}
    try:
        if env:
            os.environ.update(env)
        for name in unset:
            os.environ.pop(name, None)
        importlib.reload(config)
        yield config
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        importlib.reload(config)


def _repo_root() -> Path:
    """Worktree root, anchored at the package location (config.py's parents[2])."""
    return Path(config.__file__).resolve().parents[2]


class TestDefaultPath:
    """Default (env var unset) db_path points at the repo-root db/ directory."""

    def test_default_path_ends_with_db_analytics_duckdb(self):
        """Acceptance: default db_path endswith ``db/analytics.duckdb``."""
        with _reloaded_config(unset=(ENV_VAR,)) as cfg:
            assert cfg.db_path.endswith(DB_SUFFIX)
            # Same check, separator-agnostic.
            assert Path(cfg.db_path).parts[-2:] == (DB_DIRNAME, DB_FILENAME)

    def test_default_path_resolves_to_repo_root_db_dir(self):
        """The unnormalized default (``.../../..``) resolves to <repo_root>/db/."""
        expected = _repo_root() / DB_DIRNAME / DB_FILENAME
        with _reloaded_config(unset=(ENV_VAR,)) as cfg:
            assert Path(cfg.db_path).resolve() == expected
            # Not buried inside the package directory.
            assert Path(cfg.db_path).resolve().parent.parent == _repo_root()

    def test_default_path_parent_directory_is_created(self):
        """makedirs side effect: the default db/ directory exists after import."""
        with _reloaded_config(unset=(ENV_VAR,)) as cfg:
            parent = Path(cfg.db_path).resolve().parent
            assert parent.is_dir()
            assert parent.name == DB_DIRNAME


class TestEnvOverride:
    """WRM_DUCKDB_PATH overrides the default; local paths get their dir created."""

    def test_env_override_honored_and_directory_created(self, tmp_path):
        """Acceptance: override value wins verbatim and its parent dir is made."""
        override = tmp_path / "custom" / "nested" / "my.duckdb"
        with _reloaded_config(env={ENV_VAR: str(override)}) as cfg:
            assert cfg.db_path == str(override)
            assert override.parent.is_dir(), "makedirs did not run for override"

    def test_env_override_relative_path_resolves_against_cwd(self, tmp_path, monkeypatch):
        """A relative override is used verbatim; makedirs resolves it via CWD."""
        monkeypatch.chdir(tmp_path)
        with _reloaded_config(env={ENV_VAR: "alt/nested/analytics.duckdb"}) as cfg:
            assert cfg.db_path == "alt/nested/analytics.duckdb"
            assert (tmp_path / "alt" / "nested").is_dir()

    def test_env_override_empty_string_yields_empty_path(self, tmp_path, monkeypatch):
        """Documents current semantics: an empty value is still an override.

        ``os.environ.get`` returns ``''`` when the variable is set to empty, so
        the default is bypassed and db_path becomes ``''``. Flagged in the step
        report as a design ambiguity (an empty path would be rejected by a real
        DuckDB IO manager); this test only pins observed behavior.
        """
        monkeypatch.chdir(tmp_path)
        with _reloaded_config(env={ENV_VAR: ""}) as cfg:
            assert cfg.db_path == ""

    @pytest.mark.parametrize(
        "uri",
        [
            "s3://bucket/nested/analytics.duckdb",
            "gs://bucket/nested/analytics.duckdb",
            "https://storage.example.com/nested/analytics.duckdb",
        ],
        ids=["s3", "gs", "https"],
    )
    def test_remote_uri_override_skips_directory_creation(self, tmp_path, monkeypatch, uri):
        """URIs containing '://' must not trigger local makedirs."""
        monkeypatch.chdir(tmp_path)  # observe any accidental local writes
        with _reloaded_config(env={ENV_VAR: uri}) as cfg:
            assert cfg.db_path == uri
            # Without the '://' guard, makedirs(dirname(abspath(uri))) would
            # create a bogus 's3:' / 'gs:' / 'https:' directory under the CWD.
            assert list(tmp_path.iterdir()) == [], "remote URI triggered local makedirs"


class TestResourceWiring:
    """resources.py wires the local DuckDB IO managers to config.db_path."""

    def test_resources_db_path_binding_matches_config(self):
        """The by-value import chain (config -> resources) stays consistent."""
        assert resources.db_path == config.db_path

    @pytest.mark.skipif(IO_MANAGER_AT_IMPORT is None, reason=NO_MANAGER_REASON)
    def test_duckdb_io_manager_database_matches_db_path(self):
        """Acceptance: duckdb_io_manager.database == db_path."""
        assert resources.duckdb_io_manager.database == resources.db_path
        assert resources.duckdb_io_manager.database == config.db_path
        assert resources.duckdb_io_manager.schema == "wrm_analytics"

    @pytest.mark.skipif(HYBRID_IO_MANAGER_AT_IMPORT is None, reason=NO_MANAGER_REASON)
    def test_duckdb_hybrid_io_manager_database_matches_db_path(self):
        """Acceptance: duckdb_hybrid_io_manager.database == db_path."""
        assert resources.duckdb_hybrid_io_manager.database == resources.db_path
        assert resources.duckdb_hybrid_io_manager.database == config.db_path
        assert resources.duckdb_hybrid_io_manager.schema == "wrm_analytics"

    @pytest.mark.skipif(S3_IO_MANAGER_AT_IMPORT is None, reason=NO_MANAGER_REASON)
    def test_duckdb_s3_io_manager_keeps_s3_uri(self):
        """S3-step manager untouched: database stays a remote s3:// URI."""
        database = resources.duckdb_s3_io_manager.database
        assert database.startswith("s3://")
        assert database.endswith("/duckdb/analytics.duckdb")

    @pytest.mark.skipif(
        DUCKDB_PANDAS_INSTALLED,
        reason="dagster-duckdb-pandas installed; the real-manager tests above cover wiring",
    )
    def test_duckdb_io_managers_wired_to_config_db_path(self, tmp_path, monkeypatch):
        """Verify resources.py wiring without dagster-duckdb-pandas installed.

        Injects a stub ``dagster_duckdb_pandas`` module so the optional import
        in resources.py succeeds, reloads resources, asserts both local managers
        received ``database=db_path``, then restores the module state (managers
        back to None).
        """
        stub = types.ModuleType("dagster_duckdb_pandas")

        class StubIOManager:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        stub.DuckDBPandasIOManager = StubIOManager
        sys.modules["dagster_duckdb_pandas"] = stub
        try:
            with _reloaded_config(env={ENV_VAR: str(tmp_path / "wired.duckdb")}):
                # Re-import inside the patched env so resources picks up the stub.
                importlib.reload(resources)
                io_manager = resources.duckdb_io_manager
                hybrid = resources.duckdb_hybrid_io_manager
                assert io_manager is not None
                assert io_manager.database == config.db_path == str(tmp_path / "wired.duckdb")
                assert io_manager.schema == "wrm_analytics"
                assert hybrid is not None
                assert hybrid.database == config.db_path
                # S3-step manager untouched: still a remote s3:// URI.
                assert resources.duckdb_s3_io_manager.database.startswith("s3://")
                assert resources.duckdb_s3_io_manager.database.endswith(
                    "/duckdb/analytics.duckdb"
                )
        finally:
            sys.modules.pop("dagster_duckdb_pandas", None)
            importlib.reload(resources)  # managers back to None in this env


class TestNoStaleDataPath:
    """Guard: the old ~/data duckdb location must not reappear in Python code."""

    def test_no_stale_home_data_references_in_python_sources(self):
        """Acceptance: grep for ~/data duckdb references in *.py -> zero hits."""
        package_root = Path(config.__file__).resolve().parent
        stale_markers = ("~/data", "/root/data")
        offenders = []
        for py_file in sorted(package_root.rglob("*.py")):
            text = py_file.read_text(encoding="utf-8", errors="replace")
            if any(marker in text for marker in stale_markers):
                offenders.append(str(py_file.relative_to(package_root)))
        assert offenders == [], f"stale ~/data path references in: {offenders}"
