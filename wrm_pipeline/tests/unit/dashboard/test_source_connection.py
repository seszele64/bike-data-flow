"""Evidence tests for the S9.2 wrm source connection (``connection.yaml``).

Contract under test
-------------------
S9.2 added ``dashboard/sources/wrm/connection.yaml`` pointing Evidence's
duckdb connector at the pipeline-written datasets. S3-02..S3-04 moved the
contract off the S9.1 local snapshot
(``evidence_data_snapshot`` → ``dashboard/sources/wrm/wrm.duckdb``) to S3
parquet: ``evidence_data_snapshot`` exports
``s3://<BUCKET_NAME>/wrm/*.parquet`` and the ``.sql`` sources read them via
``read_parquet()`` (S3-03). ``connection.yaml`` stays credential-free
(S3-04); its ``options.filename`` is a retained driver-compat handle only
(``@evidence-dev/duckdb`` requires ``filename`` and opens it READ_ONLY) —
no local snapshot file is written or required (S3-01/S3-02).

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

Installed state (S9.2 + B4.3 — gap closed, was deferred by design)
------------------------------------------------------------------
B4.2 closed the config seam: ``evidence.config.yaml`` declares
``plugins.datasources: {"@evidence-dev/duckdb": {}}``.
B4.3 closed the install seam: ``npm install`` installed
``@evidence-dev/duckdb@2.0.1`` under ``dashboard/node_modules`` (locked in
``dashboard/package-lock.json`` as ``node_modules/@evidence-dev/duckdb`` and
in ``dashboard/package.json`` devDependencies alongside
``@evidence-dev/evidence`` and ``typescript``), so the registry now provides
``duckdb`` and live ``evidence sources`` discovery runs hermetically.
These tests (a) assert every static precondition so the
``connection.yaml`` itself is NOT skipped or rejected at load time,
(b) lock the installed state so the closure stays machine-checked rather
than accidental, and (c) run a live, hermetic discovery run
(``SEND_ANONYMOUS_USAGE_STATS=false``, no network needed) — the acceptance
criterion "discovery not-skipped asserted" (the old "OR documented skip
due to missing plugin" arm is retired now the plugin is installed).

Test strategy
-------------
Artifact-locked assertions against the recorded files (parse, exact keys,
parquet SQL contract in the ``.sql`` sources, credential-free connection),
plus a pure-Python port of the zod spec schema (``_validate_spec``) so
malformed-input edge cases run parametrically without ever touching the
recorded artifact. No test asserts the existence or contents of a local
``wrm.duckdb`` file (S3-01/S3-02: untracked, never written).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

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
STATIONS_SQL = os.path.join(WRM_SOURCE_DIR, "stations_latest.sql")
DENSITY_SQL = os.path.join(WRM_SOURCE_DIR, "density_grid.sql")
EVIDENCE_CONFIG = os.path.join(DASHBOARD_DIR, "evidence.config.yaml")

# S9.2 recorded contract values, as amended by S3-02..S3-04 (parquet contract).
EXPECTED_NAME = "wrm"
EXPECTED_TYPE = "duckdb"
# S3-04: retained driver-compat handle only — @evidence-dev/duckdb requires
# `filename` (getRunner logs "Missing required duckdb option 'filename'")
# and opens it READ_ONLY. It is NOT a snapshot path: no local wrm.duckdb
# file is written (S3-02) or required (S3-01), so no test asserts its
# existence — see TestParquetContract below.
EXPECTED_OPTIONS = {"filename": "wrm.duckdb"}
# Dual-write: the .sql sources read the local wrm.duckdb snapshot via FROM main.*.
EXPECTED_MAIN_TABLES = {
    "stations_latest": "main.stations_latest",
    "density_grid": "main.density_grid",
}
# Tokens that must never appear as option keys/values (credential-free S3-04).
# Checked against parsed option keys + string values, not raw comment text
# (the yaml header legitimately contains the word "secrets" in prose).
_FORBIDDEN_OPTION_KEYS = ("secret", "access_key", "endpoint", "token", "password")
_FORBIDDEN_ENV_PREFIXES = ("S3_", "HETZNER_", "AWS_")

# B4.3 seams: package-lock.json stays hard; the materialized node_modules tree
# gates (skipif not installed) because it only exists after `npm install`.
_DUCKDB_TREE_DIR = os.path.join(DASHBOARD_DIR, "node_modules", "@evidence-dev", "duckdb")
requires_duckdb_tree = pytest.mark.skipif(
    not os.path.isdir(_DUCKDB_TREE_DIR),
    reason="@evidence-dev/duckdb not materialized under dashboard/node_modules (run npm install)",
)

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
    if not isinstance(name, str) or not SPEC_NAME_PATTERN.fullmatch(name):
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
    if os.path.isdir(_DUCKDB_TREE_DIR):
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
    """dashboard/sources/wrm/connection.yaml is exactly the S3-04 stub."""

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
        assert SPEC_NAME_PATTERN.fullmatch(spec_raw["name"])

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
        assert (spec_raw.get("options") or {}) == EXPECTED_OPTIONS

    def test_source_name_matches_directory_name(self, spec_raw: dict):
        """Queries address datasets as `<yaml name>.<query>`; dir must agree."""
        assert spec_raw["name"] == os.path.basename(WRM_SOURCE_DIR)

    def test_filename_is_retained_driver_handle_not_snapshot(self, spec_raw: dict):
        """S3-04: `filename` is kept only because the duckdb driver requires it.

        It is a bare relative handle opened READ_ONLY — NOT a snapshot path,
        so this test deliberately asserts NO file existence (S3-01/S3-02:
        the local wrm.duckdb is untracked and never written). The yaml
        header documents the driver requirement and the parquet contract.
        """
        assert spec_raw["options"] == EXPECTED_OPTIONS
        filename = spec_raw["options"]["filename"]
        assert "/" not in filename  # bare handle, resolved relative to source dir
        with open(CONNECTION_YAML) as fh:
            header = fh.read()
        lowered = header.lower()
        assert "read_parquet" in lowered or "parquet" in lowered
        assert "filename" in lowered

    def test_connection_is_credential_free(self, spec_raw: dict):
        """S3-04: no secrets embedded in connection options (ambient creds only)."""
        options = spec_raw.get("options") or {}
        for key, value in options.items():
            assert not any(tok in key.lower() for tok in _FORBIDDEN_OPTION_KEYS), key
            if isinstance(value, str):
                assert not any(tok in value.lower() for tok in _FORBIDDEN_OPTION_KEYS), value
                assert "://" not in value or value == EXPECTED_OPTIONS["filename"]
        for key in os.environ:
            if key.startswith("EVIDENCE_SOURCE__") and key.lower().startswith(
                f"evidence_source__{EXPECTED_NAME.lower()}__"
            ):
                pytest.fail(f"env override seam must stay empty: {key}")

    def test_sql_sources_read_from_main(self):
        """Dual-write local-first: both .sql sources query FROM main.* in wrm.duckdb."""
        for sql_path, table in (
            (STATIONS_SQL, "stations_latest"),
            (DENSITY_SQL, "density_grid"),
        ):
            assert os.path.isfile(sql_path), f"missing source query: {sql_path}"
            with open(sql_path) as fh:
                sql = fh.read()
            lowered = sql.lower()
            assert f"from {EXPECTED_MAIN_TABLES[table]}" in lowered
            assert "access_key" not in lowered
            assert "secret_access" not in lowered
            assert "hetzner" not in lowered


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
        ["", "wr m", "wrm.dash", "wrm/dash", "wrm!", "wrm🚲", "wrm\n", "-ok dash but.dot has space"],
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
# Plugin registry: B4.3 installed state (machine-checked, not assumed).
# --------------------------------------------------------------------------- #
class TestPluginRegistryInstalled:
    """Step 4/5 of the discovery chain — B4.3 gap closed (config + install)."""

    def test_evidence_config_declares_duckdb_datasource(self):
        """B4.2+: evidence.config.yaml registers @evidence-dev/duckdb for wrm/."""
        config = _read_yaml(EVIDENCE_CONFIG)
        assert (config.get("plugins") or {}).get("datasources") == {"@evidence-dev/duckdb": {}}

    def test_duckdb_plugin_installed(self):
        """B4.3: @evidence-dev/duckdb is installed (tree AND lockfile seams)."""
        # Lockfile seam stays hard: package-lock.json must declare the plugin
        # even where `npm install` has not materialized node_modules.
        with open(os.path.join(DASHBOARD_DIR, "package-lock.json")) as fh:
            lockfile = json.load(fh)
        assert "node_modules/@evidence-dev/duckdb" in lockfile.get("packages", {})
        # Tree seam is gated (skipif not installed): on-disk copy needed by
        # loadPluginPackage resolves only after `npm install`.
        if not os.path.isdir(_DUCKDB_TREE_DIR):
            pytest.skip(
                "@evidence-dev/duckdb not materialized under node_modules (run npm install)"
            )
        assert _duckdb_plugin_installed()
        assert os.path.isdir(_DUCKDB_TREE_DIR)

    @requires_duckdb_tree
    def test_registry_provides_duckdb_type(self):
        """The registered plugin provides the `duckdb` source type."""
        assert EXPECTED_TYPE in _registered_source_types()

    @requires_duckdb_tree
    def test_discovery_state_is_self_consistent(self):
        """Registry provides duckdb AND plugin installed: the chain can't be half-open."""
        registered = _registered_source_types()
        installed = _duckdb_plugin_installed()
        assert installed, "B4.3 requires @evidence-dev/duckdb to be installed"
        assert EXPECTED_TYPE in registered, (
            "loadSourcePlugins resolves the config key from node_modules; "
            "an installed plugin with a config key must register its type."
        )

    @requires_duckdb_tree
    def test_discovery_gap_closed_resolves_duckdb(self):
        """Acceptance gate (first arm): `type: duckdb` resolves when installed.

        B4.3 retired the documented-skip arm: the plugin is installed and the
        registry is non-empty, so this hard-asserts resolution and the live
        CLI test in TestLiveDiscovery executes (gated only by npm availability
        and the materialized node_modules tree skipif).
        """
        registered = _registered_source_types()
        assert _duckdb_plugin_installed()
        assert EXPECTED_TYPE in registered


# --------------------------------------------------------------------------- #
# Live discovery (gated only by npm + materialized node_modules tree).
# --------------------------------------------------------------------------- #
class TestLiveDiscovery:
    """`evidence sources` resolves wrm without the loader/plugin skips."""

    @requires_duckdb_tree
    def test_evidence_sources_processes_wrm(self):
        """Run real discovery with @evidence-dev/duckdb registered.

        B4.3: hermetic (SEND_ANONYMOUS_USAGE_STATS=false, no network), and
        asserts the source is *processed* — not loader-skipped, not
        plugin-not-found, exit 0. The wrm source ships S3-03 .sql queries
        (read_parquet over s3://bike-data/wrm/*.parquet); discovery must
        process them without requiring any local wrm.duckdb file.
        Only npm-availability or an unmaterialized node_modules tree may skip.
        """
        npm = shutil.which("npm")
        if npm is None:
            pytest.skip("npm not available; cannot run live Evidence discovery")
        from wrm_pipeline.assets import dashboard as dashboard_module

        registered = _registered_source_types()
        assert _duckdb_plugin_installed(), "B4.3 requires the duckdb plugin installed"
        assert EXPECTED_TYPE in registered

        # S3/A1 F3: reuse the prod credential-free env filter (strips
        # HETZNER_*/S3_*) instead of passing full os.environ to the child.
        env = dict(dashboard_module._build_env(), SEND_ANONYMOUS_USAGE_STATS="false")
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
