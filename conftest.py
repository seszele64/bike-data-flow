"""Root pytest bootstrap.

Makes plain `pytest` work from the repository root: markers used with
``--strict-markers`` are registered (idempotently, in addition to the
declaration in ``pyproject.toml``), tests under a ``tests/integration``
directory are auto-marked, and shared fixtures used across suites live here.
"""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest

# Pandera emits a noisy FutureWarning at import time on every run; silence it
# before any test module (or wrm_pipeline) import pulls pandera in.
os.environ.setdefault("DISABLE_PANDERA_IMPORT_WARNING", "True")

# Declared in pyproject.toml [tool.pytest.ini_options].markers; registered here
# as well so --strict-markers stays satisfied even under other ini files.
KNOWN_MARKERS: tuple[tuple[str, str], ...] = (
    ("integration", "touches external systems (S3, Vault, live APIs); select with -m integration"),
    ("evidence", "asserts against recorded baseline/evidence artifacts"),
    ("slow", "long-running tests excluded from the fast feedback loop"),
)


def pytest_configure(config: pytest.Config) -> None:
    """Register shared markers, skipping any already declared in an ini file."""
    registered = {
        line.split(":", 1)[0].strip() for line in config.getini("markers")
    }
    for name, description in KNOWN_MARKERS:
        if name not in registered:
            config.addinivalue_line("markers", f"{name}: {description}")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-apply the `integration` marker to tests collected from tests/integration."""
    for item in items:
        if item.path.parent.name == "integration":
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def mock_s3_resource() -> Mock:
    """Generic mocked Dagster S3 resource.

    Class-level fixtures of the same name in test modules shadow this one;
    it exists for new suites (e.g. integration tests) that need the same seam.
    """
    return Mock(name="s3_resource")


@pytest.fixture
def asset_context(mock_s3_resource: Mock):
    """Dagster AssetExecutionContext wired to ``mock_s3_resource``."""
    from dagster import build_asset_context

    return build_asset_context(resources={"s3_resource": mock_s3_resource})
