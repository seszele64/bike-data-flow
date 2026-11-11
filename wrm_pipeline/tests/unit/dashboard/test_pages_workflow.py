"""Unit tests for the Pages workflow publish/skip gate (S5).

Contract under test (``.github/workflows/pages.yml``):
- ``on``: push(main) + workflow_dispatch + repository_dispatch
  ``[wrm-parquet-updated]`` + schedule cron ``30 5 * * *``.
- ``build`` job probes freshness into ``steps.freshness.outputs.stale`` and
  exposes it as the ``build.stale`` job output, plus the probe-computed
  ``steps.freshness.outputs.fingerprint`` as the ``build.fingerprint`` output.
- The probe writes the fingerprint *only* to ``$GITHUB_OUTPUT``; the value is
  container metadata and must never be echoed into the job log.
- Publish gate: the four build steps that produce the artifact run only when
  ``github.event_name != 'schedule' || steps.freshness.outputs.stale == 'true'``
  (push/dispatch always publish; a scheduled run with unchanged parquet skips).
- ``deploy`` gate: ``github.ref == 'refs/heads/main'`` AND
  (``event != schedule`` OR ``needs.build.outputs.stale == 'true'``).
- TOCTOU guard: ``record-freshness`` receives ``build.fingerprint`` in a
  non-secret step env var (``BUILD_FP``), recomputes the S3 fingerprint and
  writes the marker only when the two still match; on mismatch it warns and
  skips the ``put-object`` so the next scheduled run re-publishes.
- ``set -u`` safety: the probe normalizes each injected S3 secret with
  ``${VAR:-}`` before its first use, so a genuinely unset variable cannot
  abort the step ahead of ``emit`` and break the fail-open contract.

Strategy: pure unit, offline, no network/secrets. Parses pages.yml as YAML and
pins the exact gate strings. No credential values are read or written.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml

pytestmark = pytest.mark.evidence

REPO_ROOT = os.path.abspath(
    # tests/unit/dashboard/test_pages_workflow.py -> 4 levels up
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "pages.yml")

# Steps gated so an unchanged scheduled run produces no artifact.
PUBLISH_STEPS = (
    "Assemble wrm.duckdb from S3 parquet",
    "Fetch sources",
    "Clean previous build output",
    "Build Evidence site",
    "Upload Pages artifact",
)
PUBLISH_IF = "github.event_name != 'schedule' || steps.freshness.outputs.stale == 'true'"


def _load() -> dict:
    with open(WORKFLOW_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "pages.yml must parse to a mapping"
    return data


def _on(data: dict) -> dict:
    # YAML 1.1 parses a bare `on` key as boolean True.
    on = data.get("on", data.get(True))
    assert isinstance(on, dict), "`on` must be a mapping"
    return on


def _build_steps() -> list[dict]:
    return _load()["jobs"]["build"]["steps"]


def _probe_step() -> dict:
    return next(
        s for s in _build_steps() if s.get("name") == "Check S3 parquet freshness"
    )


def _record_job() -> dict:
    return _load()["jobs"]["record-freshness"]


def _record_step() -> dict:
    return _record_job()["steps"][0]


def _marker_lines(run: str) -> dict[str, str]:
    """Pin the two constants that define *what* the fingerprint covers."""
    wanted = {
        line.split("=", 1)[0]: line.strip()
        for line in run.splitlines()
        if line.strip().startswith(("MARKER_KEY=", "PARQUET_KEYS="))
    }
    assert set(wanted) == {"MARKER_KEY", "PARQUET_KEYS"}
    return wanted


def _uses_errexit(run: str) -> bool:
    """True when an actual `set -e`/`set -euo` runs (comments excluded)."""
    code = "\n".join(
        line for line in run.splitlines() if not line.lstrip().startswith("#")
    )
    return re.search(r"\bset\s+-[a-z]*e[a-z]*\b", code) is not None


def _fingerprint_function(run: str) -> list[str]:
    """Normalized body of `fingerprint()` so probe and recorder can be diffed."""
    match = re.search(r"^fingerprint\(\) \{\n(.*?)\n\s*\}$", run, re.MULTILINE | re.DOTALL)
    assert match, "fingerprint() helper not found"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
class TestTriggers:
    def test_dispatch_type_wrm_parquet_updated(self):
        on = _on(_load())
        dispatch = on["repository_dispatch"]
        assert dispatch["types"] == ["wrm-parquet-updated"]

    def test_schedule_cron_30_5(self):
        on = _on(_load())
        crons = [entry["cron"] for entry in on["schedule"]]
        assert crons == ["30 5 * * *"]

    def test_push_main_and_workflow_dispatch_present(self):
        on = _on(_load())
        assert set(on["push"]["branches"]) == {"main"}
        assert "workflow_dispatch" in on


# --------------------------------------------------------------------------- #
# Freshness gate plumbing
# --------------------------------------------------------------------------- #
class TestFreshnessPlumbing:
    def test_build_job_exposes_stale_output(self):
        out = _load()["jobs"]["build"]["outputs"]
        assert out["stale"] == "${{ steps.freshness.outputs.stale }}"

    def test_build_job_exposes_fingerprint_output(self):
        """The exact probe fingerprint is handed to record-freshness via needs."""
        out = _load()["jobs"]["build"]["outputs"]
        assert out["fingerprint"] == "${{ steps.freshness.outputs.fingerprint }}"

    def test_build_fingerprint_handoff_is_single_sited(self):
        """One producer (probe output) and one consumer (record step env)."""
        with open(WORKFLOW_PATH, encoding="utf-8") as fh:
            text = fh.read()
        assert text.count("${{ steps.freshness.outputs.fingerprint }}") == 1
        assert text.count("${{ needs.build.outputs.fingerprint }}") == 1

    def test_freshness_step_id(self):
        step = next(
            s for s in _build_steps() if s.get("name") == "Check S3 parquet freshness"
        )
        assert step["id"] == "freshness"

    def test_freshness_emits_stale_output(self):
        step = next(
            s for s in _build_steps() if s.get("name") == "Check S3 parquet freshness"
        )
        assert "stale=" in step["run"]
        assert "GITHUB_OUTPUT" in step["run"]


# --------------------------------------------------------------------------- #
# Publish gate: skip vs publish
# --------------------------------------------------------------------------- #
class TestPublishGate:
    def test_all_artifact_steps_carry_the_gate(self):
        steps = {s.get("name"): s for s in _build_steps()}
        for name in PUBLISH_STEPS:
            assert name in steps, f"missing step {name!r}"
            assert steps[name].get("if") == PUBLISH_IF, name

    def test_gate_is_schedule_or_stale_true(self):
        """Publish unless: scheduled AND not stale (i.e. unchanged parquet)."""
        assert "event_name != 'schedule'" in PUBLISH_IF
        assert "steps.freshness.outputs.stale == 'true'" in PUBLISH_IF

    def test_freshness_probe_is_ungated(self):
        """The probe must always run so `stale` is always produced."""
        step = next(
            s for s in _build_steps() if s.get("name") == "Check S3 parquet freshness"
        )
        assert "if" not in step


# --------------------------------------------------------------------------- #
# Deploy gate: main branch + skip-on-unchanged-schedule
# --------------------------------------------------------------------------- #
class TestDeployGate:
    def test_deploy_requires_main_branch(self):
        deploy_if = _load()["jobs"]["deploy"]["if"]
        assert "github.ref == 'refs/heads/main'" in deploy_if

    def test_deploy_gate_mirrors_publish_gate_via_needs_output(self):
        deploy_if = _load()["jobs"]["deploy"]["if"]
        assert "github.event_name != 'schedule'" in deploy_if
        assert "needs.build.outputs.stale == 'true'" in deploy_if

    def test_deploy_needs_build(self):
        deploy = _load()["jobs"]["deploy"]
        assert deploy["needs"] == "build"


# --------------------------------------------------------------------------- #
# Read-only freshness probe
# --------------------------------------------------------------------------- #
class TestProbeIsReadOnly:
    """The build probe may read the marker but must never advance it.

    Premature-write regression: recording the fingerprint inside `build` (or
    before `deploy` succeeds) marks data fresh even when the publish failed,
    so the next scheduled run would skip and the site would stay stale.
    """

    def test_probe_has_no_write_commands(self):
        run = _probe_step()["run"]
        for command in (
            "put-object",
            "copy-object",
            "delete-object",
            "s3 cp",
            "s3 mv",
            "s3 sync",
        ):
            assert command not in run, command

    def test_only_post_deploy_step_writes_the_marker(self):
        """Exactly one marker write in the workflow, inside record-freshness."""
        writes = []
        for job_name, job in _load()["jobs"].items():
            for step in job.get("steps", []):
                if "put-object" in step.get("run", ""):
                    writes.append((job_name, step.get("name")))
        assert writes == [("record-freshness", "Record parquet freshness fingerprint")]

    def test_probe_still_reads_previous_marker(self):
        run = _probe_step()["run"]
        assert "get-object" in run
        assert "OLD_FP" in run
        # It compares the stored fingerprint to decide staleness.
        assert 'OLD_FP" = "$NEW_FP' in run

    def test_probe_fails_open(self):
        """No `set -e`; unknown/error defaults to stale and exits 0."""
        run = _probe_step()["run"]
        assert not _uses_errexit(run)
        assert "set -uo pipefail" in run
        assert 'STALE="true"' in run
        assert "if command -v aws" in run
        assert "exit 1" not in run
        assert run.rstrip().endswith("exit 0")


# --------------------------------------------------------------------------- #
# Probe fingerprint output: GITHUB_OUTPUT only, never logged
# --------------------------------------------------------------------------- #
class TestProbeFingerprintOutput:
    def test_fingerprint_is_emitted_to_github_output_only(self):
        """Exactly one write: the value goes to the output file, not stdout."""
        run = _probe_step()["run"]
        writes = [
            line.lstrip()
            for line in run.splitlines()
            if not line.lstrip().startswith("#") and "fingerprint=" in line
        ]
        assert writes, "probe must emit a fingerprint output"
        for line in writes:
            assert line.startswith("printf 'fingerprint=%s"), line
            assert '>> "$GITHUB_OUTPUT"' in line, line

    def test_fingerprint_value_never_echoed(self):
        """No echo/printf may print the raw fingerprint or its variable."""
        run = _probe_step()["run"]
        for line in run.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#") or not stripped.startswith(
                ("echo ", "printf ")
            ):
                continue
            # The GITHUB_OUTPUT write redirects to the file; stdout stays clean.
            if '>> "$GITHUB_OUTPUT"' in line:
                continue
            assert "$NEW_FP" not in line, line
            assert "${NEW_FP}" not in line, line
            assert "fingerprint=" not in line, line

    def test_empty_fingerprint_is_not_published(self):
        """Fail-open probe (no fingerprint) leaves the output unset."""
        run = _probe_step()["run"]
        guard = run.index('if [ -n "${2:-}" ]; then')
        write = run.index("printf 'fingerprint=%s")
        stale = run.index("printf 'stale=%s")
        emit = run.index('emit "$STALE" "$NEW_FP"')
        assert stale < guard < write < emit
        assert "printf 'fingerprint=%s\\n' \"$2\"" in run


# --------------------------------------------------------------------------- #
# Post-deploy freshness recording
# --------------------------------------------------------------------------- #
class TestRecordFreshnessJob:
    def test_needs_build_and_deploy(self):
        assert _record_job()["needs"] == ["build", "deploy"]

    def test_gated_on_both_successes_and_not_cancelled(self):
        gate = _record_job()["if"]
        assert "!cancelled()" in gate
        assert "needs.build.result == 'success'" in gate
        assert "needs.deploy.result == 'success'" in gate
        # ANDed, never ORed: a skipped deploy must not advance the marker.
        assert "&&" in gate
        assert "||" not in gate

    def test_record_step_writes_the_marker_after_deploy(self):
        run = _record_step()["run"]
        assert "put-object" in run
        assert "MARKER_KEY" in run
        # Recomputes after the deploy rather than reusing the build probe value.
        assert "fingerprint" in run and "NEW_FP" in run

    def test_record_step_receives_build_fingerprint_env(self):
        """The build fingerprint arrives as a non-secret needs output."""
        assert _record_step()["env"] == {
            "BUILD_FP": "${{ needs.build.outputs.fingerprint }}"
        }

    def test_recorder_compares_recomputed_fingerprint_with_build(self):
        run = _record_step()["run"]
        assert '[ -z "${BUILD_FP:-}" ] || [ "$NEW_FP" != "$BUILD_FP" ]' in run
        guard = run.index('"$NEW_FP" != "$BUILD_FP"')
        put = run.index("s3api put-object")
        assert guard < put, "mismatch guard must run before the marker write"

    def test_mismatch_warns_and_skips_marker_write(self):
        """Changed data between build and record must not be marked published."""
        run = _record_step()["run"]
        branch = run[
            run.index('"$NEW_FP" != "$BUILD_FP"') : run.index("s3api put-object")
        ]
        assert "::warning::" in branch
        assert "Parquet changed between build and freshness recording" in branch
        assert "exit 0" in branch
        # An unset BUILD_FP is treated the same as a mismatch (conservative).
        assert '[ -z "${BUILD_FP:-}" ]' in run


# --------------------------------------------------------------------------- #
# Fingerprint contract: probe and recorder must agree byte-for-byte
# --------------------------------------------------------------------------- #
class TestFingerprintContract:
    def test_marker_key_and_parquet_inputs_match(self):
        assert _marker_lines(_probe_step()["run"]) == _marker_lines(
            _record_step()["run"]
        )

    def test_fingerprint_function_bodies_are_identical(self):
        """Same ETag/Last-Modified fields, key order and hash -> same value."""
        assert _fingerprint_function(_probe_step()["run"]) == _fingerprint_function(
            _record_step()["run"]
        )

    def test_fingerprint_uses_etag_and_last_modified(self):
        for run in (_probe_step()["run"], _record_step()["run"]):
            assert "--query '[ETag,LastModified]'" in run
            assert "--output text" in run
            assert "sha256sum" in run
            keys = re.search(r"PARQUET_KEYS=\(([^)]*)\)", run).group(1).split()
            assert keys == ["wrm/stations_latest.parquet", "wrm/density_grid.parquet"]


# --------------------------------------------------------------------------- #
# Best-effort recorder: warns, exits 0, leaks nothing
# --------------------------------------------------------------------------- #
class TestRecorderBestEffort:
    def test_recorder_never_fails_the_workflow(self):
        run = _record_step()["run"]
        assert not _uses_errexit(run)
        assert "set -uo pipefail" in run
        # AWS missing, fingerprint failure, missing BUILD_FP, build/recorder
        # mismatch, put failure -> five warning paths, each ending in success.
        assert run.count("::warning::") == 5
        for message in (
            "AWS CLI unavailable",
            "Could not compute parquet freshness fingerprint",
            "Build fingerprint unavailable",
            "Parquet changed between build and freshness recording",
            "Failed to record parquet freshness marker",
        ):
            assert message in run, message
        assert run.rstrip().endswith("exit 0")

    def test_no_fingerprint_or_secret_values_are_echoed(self):
        run = _record_step()["run"]
        sensitive = (
            "$NEW_FP",
            "$OLD_FP",
            "$BUILD_FP",
            "${BUILD_FP}",
            "$meta",
            "$out",
            "$S3_ACCESS_KEY_ID",
            "$S3_SECRET_ACCESS_KEY",
            "$S3_ENDPOINT_URL",
        )
        for line in run.splitlines():
            stripped = line.lstrip()
            if not stripped.startswith(("echo ", "printf ")):
                continue
            # Hash input is piped to sha256sum and the marker body is written
            # to a file; neither sends a raw value to the job log.
            if "| sha256sum" in line or ">/tmp" in line:
                continue
            for token in sensitive:
                assert token not in line, line

    def test_recorder_needs_no_checkout_or_node(self):
        job = _record_job()
        assert len(job["steps"]) == 1
        step = job["steps"][0]
        assert "uses" not in step
        run = step["run"]
        assert "checkout" not in run
        assert "npm" not in run
        assert "actions/" not in run


# --------------------------------------------------------------------------- #
# Secret scope: four names, exactly where S3 is touched
# --------------------------------------------------------------------------- #
SECRET_ENV = {
    "S3_ENDPOINT_URL": "${{ secrets.S3_ENDPOINT_URL }}",
    "S3_ACCESS_KEY_ID": "${{ secrets.S3_ACCESS_KEY_ID }}",
    "S3_SECRET_ACCESS_KEY": "${{ secrets.S3_SECRET_ACCESS_KEY }}",
    "BUCKET_NAME": "${{ secrets.BUCKET_NAME }}",
}
S3_CONSUMING_STEPS = {
    "Check S3 parquet freshness",
    "Assemble wrm.duckdb from S3 parquet",
    "Fetch sources",
}


class TestSecretScope:
    def test_probe_env_is_exactly_the_four_secrets(self):
        assert _probe_step()["env"] == SECRET_ENV

    def test_record_job_env_is_exactly_the_four_secrets(self):
        assert _record_job()["env"] == SECRET_ENV

    def test_record_step_env_is_only_the_non_secret_build_fingerprint(self):
        """BUILD_FP is a needs output, not a credential: allow-listed alone."""
        step_env = _record_step()["env"]
        assert step_env == {"BUILD_FP": "${{ needs.build.outputs.fingerprint }}"}
        assert not any("secrets." in str(v) for v in step_env.values())
        # The four job-level secrets stay at job level, never re-exported.
        assert not (set(step_env) & set(SECRET_ENV))

    def test_only_s3_consuming_build_steps_have_env(self):
        with_env = {
            s.get("name"): s["env"] for s in _build_steps() if "env" in s
        }
        assert set(with_env) == S3_CONSUMING_STEPS
        for name, env in with_env.items():
            assert env == SECRET_ENV, name

    def test_no_secrets_leak_to_build_or_deploy(self):
        assert "env" not in _load()["jobs"]["deploy"]
        with open(WORKFLOW_PATH, encoding="utf-8") as fh:
            text = fh.read()
        # 4 names x 4 scopes (probe, assemble, fetch, record job).
        assert text.count("${{ secrets.") == 16
        assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) == set(SECRET_ENV)


# --------------------------------------------------------------------------- #
# `set -u` safety: probe secrets are normalized before their first use
# --------------------------------------------------------------------------- #
PROBE_NORMALIZED_VARS = (
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "BUCKET_NAME",
    "S3_ENDPOINT_URL",
)


def _unsafe_expansion_offsets(run: str, var: str) -> list[int]:
    """Offsets of ``$VAR`` and ``${VAR}`` uses lacking a ``:-`` default.

    ``${VAR:-...}`` (and other braced forms that supply a fallback) are safe
    under ``set -u`` and are deliberately not matched.
    """
    pattern = rf"\$(?:{re.escape(var)}\b|\{{{re.escape(var)}\}})"
    return [m.start() for m in re.finditer(pattern, run)]


class TestProbeSecretNormalization:
    """Under ``set -u`` the fail-open probe must never touch an unset secret.

    GitHub substitutes a missing secret with the empty string, but the value
    can still be genuinely unset on some propagation paths. Every probe secret
    is therefore normalized with ``${VAR:-}`` *before* its first use; either a
    bare ``$VAR`` or a defaultless ``${VAR}`` ahead of that normalization
    aborts the step under ``set -u`` before ``emit`` writes ``stale``, which
    would break the fail-open gate. Bare uses after normalization (e.g.
    ``--bucket "$BUCKET_NAME"``) stay valid because the assignment above has
    already defined them.
    """

    @pytest.mark.parametrize("var", PROBE_NORMALIZED_VARS)
    def test_probe_normalizes_secret_with_default(self, var):
        run = _probe_step()["run"]
        normalized = f"${{{var}:-}}"
        assert normalized in run, (
            f"{var} must be normalized as {normalized} under `set -u`"
        )

    @pytest.mark.parametrize("var", PROBE_NORMALIZED_VARS)
    def test_probe_has_no_unsafe_expansion_before_normalization(self, var):
        run = _probe_step()["run"]
        normalized = f"${{{var}:-}}"
        norm_at = run.find(normalized)
        assert norm_at != -1, (
            f"{var} must be normalized as {normalized} under `set -u`"
        )
        early = [at for at in _unsafe_expansion_offsets(run, var) if at < norm_at]
        assert not early, (
            f"unsafe ${var} expansion before {normalized} under `set -u` "
            f"(offsets {early})"
        )

    @pytest.mark.parametrize("snippet", ["$VAR", "${VAR}"])
    def test_unsafe_expansion_matcher_flags_missing_default(self, snippet):
        assert _unsafe_expansion_offsets(f'use "{snippet}"', "VAR") == [5]

    @pytest.mark.parametrize("snippet", ["${VAR:-}", "${VAR:-fallback}"])
    def test_unsafe_expansion_matcher_ignores_defaulted_forms(self, snippet):
        assert _unsafe_expansion_offsets(f'use "{snippet}"', "VAR") == []
