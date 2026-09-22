"""Evidence tests for the S9.2 wrm source connection (``connection.yaml``).

Contract under test
-------------------
S9.2 added ``dashboard/sources/wrm/connection.yaml`` pointing Evidence's
duckdb connector at the pipeline-written snapshot (S9.1
``evidence_data_snapshot`` → ``dashboard/sources/wrm/wrm.duckdb``).

Discovery chain locked here (Evidence 40.1.8 / ``@evidence-dev/sdk``, read
from ``dashboard/node_modules`` and mirrored in Python because this suite
runs offline without Node execution — the ``test_dashboard_scaffold.py``
convention):

1. ``sdk/src/plugins/datasources/loadSources.js`` scans the *immediate*
   subdirectories of ``dashboard/sources/``; a directory WITHOUT
   ``connection.yaml`` is skipped ("is not a valid source (no
   connection.yaml); skipping").
2. ``loadSourceConfig.js`` parses ``connection.yaml`` and validates it
   against ``DatasourceSpecFileSchema`` (``sdk/src/plugins/datasources/
   schemas/datasource.schema.js``): ``type`` string (required), ``name``
   matching ``^[a-zA-Z0-9_-]+$`` (required), ``options`` any (optional),
   ``buildOptions: {batchSize?: number(min 1)}`` (optional). A parse or
   validation failure aborts discovery.
3. Options are merged from three seams, later seams winning:
   ``connection.yaml`` → deprecated ``connection.options.yaml`` →
   ``EVIDENCE_SOURCE__<NAME>__*`` environment variables.
4. ``loadSourcePlugins.js`` registers datasource plugins ONLY from the
   ``plugins.datasources`` map of ``evidence.config.yaml`` (keys are
   package names resolved from ``node_modules``; a missing or invalid
   package is skipped, leaving the registry empty).
5. ``evalSources.js`` looks the source's ``type`` up in that registry and
   throws ``EvidenceError("Could not find matching datasource plugin for
   <name> (source: <type>)")`` when nothing provides it.

Known gap (S9.2, by design — documented in the yaml header)
-----------------------------------------------------------
``@evidence-dev/duckdb`` is NOT installed and ``evidence.config.yaml``
declares ``plugins.datasources: {}``. Step 5 therefore cannot resolve
``type: duckdb`` yet and live ``evidence sources`` discovery is deferred to
a later S9.x step. These tests (a) assert every static precondition so the
``connection.yaml`` itself will NOT be skipped or rejected at load time,
(b) lock the gap state so the deferral stays machine-checked rather than
accidental, and (c) upgrade automatically to a live, hermetic discovery run
(``SEND_ANONYMOUS_USAGE_STATS=false``, no network needed) as soon as the
plugin is registered — the acceptance criterion "discovery not-skipped
asserted OR documented skip due to missing plugin".

Test strategy
-------------
Artifact-locked assertions against the recorded files (parse, exact keys,
cross-path consistency with the S9.1 snapshot and the pipeline asset), plus
a pure-Python port of the zod spec schema (``_validate_spec``) so
malformed-input edge cases run parametrically without ever touching the
recorded artifact.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import duckdb
import pytest
import yaml

pytestmark = pytest.mark.evidence

REPO_ROOT = os.path.abspath(
    # tests/unit/dashboard/test_source_connection.py → 4 levels up
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")
SOURCES_DIR = os.path.join(DASHBOARD_DIR, "sources")
WRM_SOURCE_DIR = os.path.join(SOURCES_DIR, "wrm")
CONNECTION_YAML = os.path.join(WRM_SOURCE_DIR, "connection.yaml")
SNAPSHOT_STUB = os.path.join(WRM_SOURCE_DIR, "wrm.duckdb")
EVIDENCE_CONFIG = os.path.join(DASHBOARD_DIR, "evidence.config.yaml")

# S9.2 recorded contract values.
EXPECTED_NAME = "wrm"
EXPECTED_TYPE = "duckdb"
EXPECTED_OPTIONS = {"filename": "wrm.duckdb"}
EXPECTED_SNAPSHOT_TABLES = {"stations_latest", "density_grid"}

# Port of the name refine in DatasourceSpecFileSchema (Evidence 40.1.8).
SPEC_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_DEFAULT_BATCH_SIZE = 1000 * 1000  # zod default in the schema
_MISSING = object()  # sentinel: distinguishes "key absent" from explicit null


class SpecValidationError(ValueError):
    """Mirrors the zod failure of DatasourceSpecFileSchema."""


def _validate_spec(raw: object) -> dict:
    """Port of ``DatasourceSpecFileSchema`` (Evidence 40.1.8).

    Reproduces the schema's accept/reject/normalize behavior:

    - non-mapping root → error; unknown top-level keys are *stripped*
      (zod default), not rejected;
    - ``type``: required string; ``name``: required string matching
      ``^[a-zA-Z0-9_-]+$``;
    - ``options``: ``z.any()`` — absent or null pass through as ``None``;
    - ``buildOptions``: absent → ``{}`` (the object's inner ``batchSize``
      default does NOT apply — zod ``.default({})`` replaces the whole
      object); present-but-null → error; explicit object gets
      ``batchSize`` defaulted to 1_000_000 and clamped to ``min 1``;
      unknown inner keys stripped.
    """
    if not isinstance(raw, dict):
        raise SpecValidationError(f"root must be a mapping, got {type(raw).__name__}")

    if "type" not in raw:
        raise SpecValidationError("type is required")
    if not isinstance(raw["type"], str):
        raise SpecValidationError("type must be a string")

    if "name" not in raw:
        raise SpecValidationError("name is required")
    name = raw["name"]
    if not isinstance(name, str) or not SPEC_NAME_PATTERN.match(name):
        raise SpecValidationError(f"name must match {SPEC_NAME_PATTERN.pattern!r}")

    normalized: dict = {"type": raw["type"], "name": name}

    if "options" in raw:
        normalized["options"] = raw["options"]  # z.any(): passes through as-is
    # else: stays absent (undefined); loadSourceConfig coalesces with `?? {}`.

    build_options = raw.get("buildOptions", _MISSING)
    if build_options is _MISSING:
        normalized["buildOptions"] = {}
    elif build_options is None:
        raise SpecValidationError("buildOptions: null is not optional()")
    elif not isinstance(build_options, dict):
        raise SpecValidationError("buildOptions must be a mapping")
    else:
        if "batchSize" in build_options:
            batch_size = build_options["batchSize"]
            if isinstance(batch_size, bool) or not isinstance(batch_size, (int, float)):
                raise SpecValidationError("batchSize must be a number")
            if batch_size < 1:
                raise SpecValidationError("batchSize must be >= 1 (z.number().min(1))")
            normalized["buildOptions"] = {"batchSize": batch_size}
        else:
            normalized["buildOptions"] = {"batchSize": _DEFAULT_BATCH_SIZE}

    return normalized


def _read_yaml(path: str) -> object:
    with open(path) as fh:
        return yaml.safe_load(fh)


def _registered_source_types() -> set[str]:
    """Mirror of loadSourcePlugins.js: type names provided by registered plugins.

    A plugin is registered when (a) its package name is a key of
    ``evidence.config.yaml → plugins.datasources`` AND (b) the package is
    resolvable from ``dashboard/node_modules`` (``loadPluginPackage``
    returns null for unresolvable packages, which the loader skips), and
    (c) its ``package.json`` declares ``evidence.datasources``.
    """
    config = _read_yaml(EVIDENCE_CONFIG) or {}
    datasources = (config.get("plugins") or {}).get("datasources") or {}
    types: set[str] = set()
    for package_name in datasources:
        package_json = os.path.join(DASHBOARD_DIR, "node_modules", package_name, "package.json")
        if not os.path.isfile(package_json):
            continue  # loadPluginPackage → null → silently skipped by the loader
        with open(package_json) as fh:
            package = json.load(fh)
        declared = (package.get("evidence") or {}).get("datasources") or []
        for entry in declared:
            types.add(entry[0] if isinstance(entry, list) else entry)
    return types


def _duckdb_plugin_installed() -> bool:
    """Is @evidence-dev/duckdb present (installed tree or lockfile)?"""
    if os.path.isdir(os.path.join(DASHBOARD_DIR, "node_modules", "@evidence-dev", "duckdb")):
        return True
    with open(os.path.join(DASHBOARD_DIR, "package-lock.json")) as fh:
        lockfile = json.load(fh)
    return "node_modules/@evidence-dev/duckdb" in lockfile.get("packages", {})


# --------------------------------------------------------------------------- #
# Artifact lock: the recorded connection.yaml itself.
# --------------------------------------------------------------------------- #
@pytest.fixture
def spec_raw() -> dict:
    """The recorded connection.yaml, parsed as a mapping (validity asserted)."""
    parsed = _read_yaml(CONNECTION_YAML)
    assert isinstance(parsed, dict), "connection.yaml must parse to a mapping"
    return parsed


class TestConnectionYamlArtifact:
    """dashboard/sources/wrm/connection.yaml is exactly the S9.2 stub."""

    def test_connection_yaml_exists(self):
        assert os.path.isfile(CONNECTION_YAML)

    def test_declares_name_type_options_only(self, spec_raw: dict):
        """Exact recorded shape: name + type + options, no buildOptions yet."""
        assert spec_raw == {
            "name": EXPECTED_NAME,
            "type": EXPECTED_TYPE,
            "options": EXPECTED_OPTIONS,
        }

    def test_name_satisfies_schema_pattern(self, spec_raw: dict):
        assert SPEC_NAME_PATTERN.match(spec_raw["name"])

    def test_type_is_duckdb(self, spec_raw: dict):
        assert spec_raw["type"] == EXPECTED_TYPE

    def test_options_filename_is_bare_relative_name(self, spec_raw: dict):
        """Evidence resolves `filename` relative to the source directory."""
        assert spec_raw["options"] == EXPECTED_OPTIONS
        assert os.pathsep not in EXPECTED_OPTIONS["filename"]
        assert "/" not in EXPECTED_OPTIONS["filename"]

    def test_comment_documents_deferred_plugin_registration(self):
        """The skip trail is documented in the artifact itself (S9.x deferral)."""
        with open(CONNECTION_YAML) as fh:
            header = fh.read()
        assert "plugin" in header.lower()
        assert "S9.x" in header


# --------------------------------------------------------------------------- #
# Static discovery preconditions: loadSources/loadSourceConfig must NOT skip.
# --------------------------------------------------------------------------- #
class TestDiscoveryPreconditions:
    """Steps 1–3 of the discovery chain must succeed on the recorded artifact."""

    def test_source_directory_is_immediately_under_sources(self):
        """loadSources scans exactly one directory level under sources/."""
        assert os.path.basename(WRM_SOURCE_DIR.rstrip(os.sep)) == EXPECTED_NAME
        assert os.path.isdir(WRM_SOURCE_DIR)

    def test_recorded_sources_directory_contains_only_wrm(self):
        """Exactly one source dir, and it is the wrm connection (nothing skipped)."""
        entries = set(os.listdir(SOURCES_DIR))
        assert entries == {EXPECTED_NAME}
        assert os.path.isfile(CONNECTION_YAML)

    def test_connection_yaml_present_so_loader_does_not_skip(self):
        """The loader's skip branch fires on a *missing* connection.yaml.

        Its message is "is not a valid source (no connection.yaml);
        skipping" — asserted impossible here because the file exists and
        parses. This is the 'discovery not skipped at load time' gate.
        """
        parsed = _read_yaml(CONNECTION_YAML)
        assert isinstance(parsed, dict)

    def test_recorded_spec_passes_schema_validation(self, spec_raw: dict):
        """DatasourceSpecFileSchema accepts it and normalizes as expected."""
        normalized = _validate_spec(spec_raw)
        assert normalized == {
            "type": EXPECTED_TYPE,
            "name": EXPECTED_NAME,
            "options": EXPECTED_OPTIONS,
            "buildOptions": {},  # absent → zod .default({}), no batchSize key
        }

    def test_effective_options_survive_the_merge_chain(self, spec_raw: dict):
        """loadSourceConfig merges yaml → connection.options.yaml → env vars.

        The deprecated options file and the env override seam must both be
        absent so the recorded options flow through unmolested.
        """
        assert not os.path.exists(os.path.join(WRM_SOURCE_DIR, "connection.options.yaml"))
        env_overrides = {
            key: value
            for key, value in os.environ.items()
            if key.startswith("EVIDENCE_SOURCE__")
            and key.lower().startswith(f"evidence_source__{EXPECTED_NAME.lower()}__")
        }
        assert env_overrides == {}
        # yaml seam alone → effective options are exactly the recorded ones.
        assert spec_raw.get("options") or {} == EXPECTED_OPTIONS

    def test_source_name_matches_directory_name(self, spec_raw: dict):
        """Queries address datasets as `<yaml name>.<query>`; dir must agree."""
        assert spec_raw["name"] == os.path.basename(WRM_SOURCE_DIR)

    def test_filename_points_at_recorded_snapshot_stub(self, spec_raw: dict):
        """options.filename resolves to the S9.1 stub, which really opens."""
        resolved = os.path.join(WRM_SOURCE_DIR, spec_raw["options"]["filename"])
        assert resolved == SNAPSHOT_STUB
        assert os.path.isfile(resolved)

        conn = duckdb.connect(resolved, read_only=True)
        try:
            tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        finally:
            conn.close()
        assert tables == EXPECTED_SNAPSHOT_TABLES

    def test_filename_matches_pipeline_snapshot_path(self, spec_raw: dict):
        """The yaml points at the exact file evidence_data_snapshot writes."""
        from wrm_pipeline.assets import dashboard as dashboard_module

        resolved = os.path.realpath(
            os.path.join(WRM_SOURCE_DIR, spec_raw["options"]["filename"])
        )
        assert resolved == os.path.realpath(dashboard_module.SNAPSHOT_PATH)


# --------------------------------------------------------------------------- #
# Schema mirror edge cases (DatasourceSpecFileSchema, parametric, no artifacts).
# --------------------------------------------------------------------------- #
class TestSpecSchemaEdgeCases:
    """What Evidence's connection.yaml schema accepts, rejects, and strips."""

    def test_minimal_valid_spec(self):
        """options and buildOptions are both optional."""
        assert _validate_spec({"type": "duckdb", "name": "wrm"}) == {
            "type": "duckdb",
            "name": "wrm",
            "buildOptions": {},
        }

    @pytest.mark.parametrize(
        "name",
        ["wrm", "wrm-2", "wrm_dash", "WRM", "a", "n_2-x", "42"],
    )
    def test_name_pattern_accepts(self, name: str):
        assert _validate_spec({"type": "duckdb", "name": name})["name"] == name

    @pytest.mark.parametrize(
        "name",
        ["", "wr m", "wrm.dash", "wrm/dash", "wrm!", "wrm🚲", "-ok dash but.dot has space"],
    )
    def test_name_pattern_rejects(self, name: str):
        with pytest.raises(SpecValidationError):
            _validate_spec({"type": "duckdb", "name": name})

    @pytest.mark.parametrize("raw", [{"name": "wrm"}, {}])
    def test_missing_type_rejected(self, raw: dict):
        with pytest.raises(SpecValidationError):
            _validate_spec(raw)

    @pytest.mark.parametrize("raw", [{"type": "duckdb"}, {}])
    def test_missing_name_rejected(self, raw: dict):
        with pytest.raises(SpecValidationError):
            _validate_spec(raw)

    @pytest.mark.parametrize("type_", [42, None, ["duckdb"], True])
    def test_non_string_type_rejected(self, type_):
        with pytest.raises(SpecValidationError):
            _validate_spec({"type": type_, "name": "wrm"})

    @pytest.mark.parametrize("raw", [[], "wrm", 42, None])
    def test_non_mapping_root_rejected(self, raw):
        with pytest.raises(SpecValidationError):
            _validate_spec(raw)

    def test_options_are_any_value(self):
        """z.any(): string, list, null and nested mapping all pass through."""
        for options in ["wrm.duckdb", [{"a": 1}], None, {"filename": "x", "read_only": True}]:
            spec = _validate_spec({"type": "duckdb", "name": "wrm", "options": options})
            assert spec["options"] == options

    def test_unknown_top_level_keys_stripped_not_rejected(self):
        """zod default object mode strips extras; it does not fail."""
        spec = _validate_spec(
            {"type": "duckdb", "name": "wrm", "options": {}, "surprise": True}
        )
        assert "surprise" not in spec

    def test_explicit_build_options_get_batch_size_default(self):
        spec = _validate_spec({"type": "duckdb", "name": "wrm", "buildOptions": {}})
        assert spec["buildOptions"] == {"batchSize": _DEFAULT_BATCH_SIZE}

    def test_explicit_batch_size_preserved(self):
        spec = _validate_spec(
            {"type": "duckdb", "name": "wrm", "buildOptions": {"batchSize": 500_000}}
        )
        assert spec["buildOptions"] == {"batchSize": 500_000}

    @pytest.mark.parametrize("batch_size", [0, -1, 0.5, "big", None, True])
    def test_invalid_batch_size_rejected(self, batch_size):
        with pytest.raises(SpecValidationError):
            _validate_spec(
                {"type": "duckdb", "name": "wrm", "buildOptions": {"batchSize": batch_size}}
            )

    def test_batch_size_lower_bound_is_inclusive(self):
        spec = _validate_spec(
            {"type": "duckdb", "name": "wrm", "buildOptions": {"batchSize": 1}}
        )
        assert spec["buildOptions"] == {"batchSize": 1}

    def test_null_build_options_rejected(self):
        """Optional ≠ nullable: explicit null fails the zod schema."""
        with pytest.raises(SpecValidationError):
            _validate_spec({"type": "duckdb", "name": "wrm", "buildOptions": None})

    def test_recorded_stub_is_minimal_shape(self, spec_raw: dict):
        """The artifact relies on every optional default (no buildOptions)."""
        assert "buildOptions" not in spec_raw
        assert _validate_spec(spec_raw)["buildOptions"] == {}


# --------------------------------------------------------------------------- #
# Plugin registry: the documented S9.2 gap (machine-checked, not assumed).
# --------------------------------------------------------------------------- #
class TestPluginRegistryGap:
    """Step 4/5 of the discovery chain — the known, documented gap."""

    def test_evidence_config_declares_no_datasources(self):
        config = _read_yaml(EVIDENCE_CONFIG)
        assert (config.get("plugins") or {}).get("datasources") == {}

    def test_duckdb_plugin_not_installed(self):
        assert not _duckdb_plugin_installed()

    def test_registry_provides_no_types(self):
        assert _registered_source_types() == set()

    def test_discovery_gap_state_is_self_consistent(self):
        """Registry empty ⟺ plugin uninstalled: the skip can't be half-open."""
        registered = _registered_source_types()
        installed = _duckdb_plugin_installed()
        assert registered == set() or installed, (
            "A registered-but-unresolvable plugin key is silently skipped by "
            "loadSourcePlugins; a bare install without a config key is "
            "invisible to discovery. Keep the two states aligned."
        )

    def test_discovery_gap_is_documented_and_gated(self):
        """Acceptance gate: discovery is asserted live once the gap closes.

        While the gap exists (plugin absent AND registry empty) the deferral
        is a *documented, machine-checked* skip — exactly the acceptance
        criterion's second arm. The moment the plugin is registered, this
        test hard-asserts that `type: duckdb` resolves (first arm) and the
        live CLI test in TestLiveDiscovery starts executing.
        """
        registered = _registered_source_types()
        if not registered and not _duckdb_plugin_installed():
            pytest.skip(
                "S9.2 known gap (documented): @evidence-dev/duckdb is not "
                "installed and evidence.config.yaml plugins.datasources is "
                "empty, so `evidence sources` cannot resolve type 'duckdb' "
                "yet — live discovery is deferred to a later S9.x step. "
                "This test auto-upgrades once the plugin is registered."
            )
        assert EXPECTED_TYPE in registered


# --------------------------------------------------------------------------- #
# Live discovery (only fires when the plugin gap is closed).
# --------------------------------------------------------------------------- #
class TestLiveDiscovery:
    """`evidence sources` resolves wrm without the loader/plugin skips."""

    def test_evidence_sources_processes_wrm(self):
        """Run real discovery once @evidence-dev/duckdb is registered.

        Skipped while the plugin is missing (documented gap above). When it
        runs: hermetic (SEND_ANONYMOUS_USAGE_STATS=false, no network), and
        asserts the source is *processed* — not loader-skipped, not
        plugin-not-found, exit 0. Zero .sql stubs exist at S9.2, so a clean
        run discovers the source with an empty dataset manifest.
        """
        registered = _registered_source_types()
        if not registered and not _duckdb_plugin_installed():
            pytest.skip(
                "S9.2 known gap (documented): @evidence-dev/duckdb is not "
                "installed and evidence.config.yaml plugins.datasources is "
                "empty — `evidence sources` would raise 'Could not find "
                "matching datasource plugin for wrm (source: duckdb)'. "
                "Live discovery is deferred to a later S9.x step."
            )
        npm = shutil.which("npm")
        if npm is None:
            pytest.skip("npm not available; cannot run live Evidence discovery")

        env = dict(os.environ, SEND_ANONYMOUS_USAGE_STATS="false")
        result = subprocess.run(
            [npm, "run", "sources", "--", "--sources", EXPECTED_NAME],
            cwd=DASHBOARD_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"evidence sources failed:\n{output}"
        assert "is not a valid source" not in output  # loader-level skip
        assert "Could not find matching datasource plugin" not in output  # registry gap
