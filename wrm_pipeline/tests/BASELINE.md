# Pre-ETL Pytest Baseline

**Step:** T4 (uv-baseline subtask) · **Recorded:** 2026-09-20

Evidence snapshot of the pytest suite **before** the ETL work begins. All counts
below were produced with `uv run pytest` in the `agent/uv-baseline` worktree and
were re-run to confirm reproducibility.

## 1. Snapshot Metadata

| Field | Value |
|---|---|
| Branch | `agent/uv-baseline` |
| Commit | `1bf4d155d8e9de5f49a0846f5169c8b0a90d1a45` (`feat: Retry Logic with Exponential Backoff (002-retry-logic) (#38)`, 2026-01-11 18:20:53 +0100) |
| Worktree | `/root/programming/bike-data-flow.agent-uv-baseline` |
| Working tree | **Dirty** — contains the uncommitted uv-migration work from T1–T3 (test relocation `wrm_pipeline_tests/` → `wrm_pipeline/tests/`, root `pyproject.toml` + `conftest.py`, `uv.lock`, removal of `requirements.txt` / `setup.py` / stale `wrm_pipeline/setup.cfg`). HEAD commit itself predates the migration. |

> The baseline therefore reflects **HEAD `1bf4d15` + the T1–T3 uncommitted
> migration changes**, i.e. the state of the worktree as recorded on 2026-09-20.
> Working-tree breakdown at capture time: 6 added, 9 modified, 6 deleted,
> 9 renamed files — all staged, nothing untracked (30 files changed,
> +3207 / −97 vs HEAD).

## 2. Environment

| Component | Version |
|---|---|
| uv | 0.10.9 |
| Python | 3.12.3 (pinned via `.python-version` = `3.12`) |
| pytest | 9.1.1 |
| pluggy | 1.6.0 |
| pytest-cov | 7.1.0 |
| dagster / dagster-aws / dagster-cloud | 1.13.23 / 0.29.23 / 1.13.23 |
| duckdb | 1.5.5 |
| pandas | 3.0.6 |
| OS | Linux 6.8.0-101-generic (x86_64) |
| Dependency lock | `uv.lock` present (121 packages, 504 KB on disk) |

Other pytest-observed plugins: `typeguard-4.6.0`, `anyio-4.15.1`.

## 3. Command & Configuration

```bash
uv run pytest          # default run; config from root pyproject.toml
```

Active pytest config (root `pyproject.toml`, `configfile: pyproject.toml`):

- `testpaths = ["wrm_pipeline/tests/unit"]` — unit tests only by default
- `pythonpath = ["."]` — repo root on `sys.path` so legacy
  `wrm_pipeline.wrm_pipeline.*` absolute imports resolve under the console
  `pytest` script
- `addopts = "-ra --strict-markers"`
- Markers registered: `integration`, `evidence`, `slow`

Integration tests are run explicitly and are **not** part of the default
baseline count:

```bash
uv run pytest wrm_pipeline/tests/integration -m integration
# → collected 0 items ("no tests ran") — suite is an empty placeholder
#   (wrm_pipeline/tests/integration/ contains only __init__.py)
```

## 4. Results

| Metric | Value |
|---|---|
| Collected | 149 |
| Passed | **149** |
| Failed / Errors | **0 / 0** |
| Skipped / Deselected / XFailed | 0 / 0 / 0 |
| Warnings in pytest output | none |

### Reproducibility (3 independent runs)

| Run | Command | Result | Duration |
|---|---|---|---|
| T3 record (prior step) | `uv run pytest` | 149 passed | 16.89 s |
| T4 run 1 (this step) | `uv run pytest -v` | 149 passed | 15.36 s |
| T4 run 2 (this step) | `uv run pytest` | 149 passed | 16.15 s |

**Count reproduces exactly (149/149) across all three runs.** Duration varies
~±10% run-to-run; treat timing as advisory only.

### Per-file breakdown

| Test file | Tests |
|---|---|
| `wrm_pipeline/tests/unit/retry/test_api_retry.py` | 30 |
| `wrm_pipeline/tests/unit/retry/test_circuit_breaker.py` | 42 |
| `wrm_pipeline/tests/unit/retry/test_config.py` | 8 |
| `wrm_pipeline/tests/unit/retry/test_s3_helpers.py` | 21 |
| `wrm_pipeline/tests/unit/retry/test_s3_retry.py` | 27 |
| `wrm_pipeline/tests/unit/stations/test_processed.py` | 14 |
| `wrm_pipeline/tests/unit/stations/test_raw.py` | 7 |
| **Total** | **149** |

Note: `grep -c "def test_"` reports 43 for `test_circuit_breaker.py`, one more
than the 42 collected — line 332 contains a nested helper named
`test_func()` inside `TestCircuitBreakerDecorator.test_decorator_creates_
circuit_breaker`, which pytest does not (and should not) collect. Not an issue.

## 5. Pre-existing Failures

**None.** At this baseline there are **0 failed, 0 errors, 0 skipped** tests.
The full list of pre-existing failures is intentionally empty — any failure
observed after the ETL work is a regression against this snapshot.

## 6. Asset Import Check (S1)

`from wrm_pipeline.definitions import defs` succeeds. `defs.assets` contains
exactly **7 assets**:

1. `bike_density_map`
2. `bike_density_spatial_analysis`
3. `duckdb_enhanced_views`
4. `station_summary`
5. `wrm_stations_enhanced_data_all`
6. `wrm_stations_processed_data_all`
7. `wrm_stations_raw_data`

Dagster API note: `Definitions.get_all_assets()` does not exist in
dagster 1.13.23; use `defs.assets` (a list) instead.

## 7. Reproducing This Baseline

```bash
cd /root/programming/bike-data-flow.agent-uv-baseline
uv sync --all-packages  # install every workspace member (creates/uses .venv from uv.lock)
uv run pytest           # expect: 149 passed
uv run python -c "from wrm_pipeline.definitions import defs; print(len(list(defs.assets)))"
                        # expect: 7
```

## 8. Known Risks / Caveats

- **Uncommitted migration state:** the baseline mixes HEAD `1bf4d15` with the
  T1–T3 working-tree changes. If those changes are committed/amended before
  ETL work starts, re-verify counts once against the new commit.
- **Timing variance:** durations range 15.36–16.89 s across runs; do not use
  duration as a pass/fail signal.
- **Empty integration suite:** `wrm_pipeline/tests/integration/` is a
  placeholder (0 tests). ETL work that adds integration tests will not be
  covered by this baseline's count.
- **pandera FutureWarning:** importing pandera emits a `FutureWarning` about
  supported libraries; it does not surface in the pytest run but may appear in
  direct interpreter imports. Harmless at this baseline.
- **Newly added tests** (e.g. the `evidence` marker suite anticipated later)
  will change the total; compare against the *pass/fail delta*, not the raw
  149 count.

---

# Post-ETL Regression Evidence

**Step:** S5 (etl-repair subtask) · **Recorded:** 2026-09-20 (21:51 +0200)

Evidence snapshot of the pytest suite **after** the ETL work (S2 parquet/S3
write in `processed_all.py`, S3 job/schedule wiring, S4 unified DuckDB path).
Same command, same worktree lineage as the pre-ETL baseline above. Nothing
below was edited retroactively; this section was appended after all runs.

## 9. Snapshot Metadata

| Field | Value |
|---|---|
| Branch | `agent-etl-repair` |
| Commit | `b2074a358e9ed34cdb7af10511ecfbd0bf906d71` (`chore: uv + pytest migration baseline (T1-T3) (#39)`) |
| Worktree | `/root/programming/bike-data-flow.agent-etl-repair` |
| Working tree | **Dirty** — contains the uncommitted S2–S4 changes: `wrm_pipeline/wrm_pipeline/assets/stations/processed_all.py` (S2, pyarrow schema validation + parquet-to-S3 write), `wrm_pipeline/wrm_pipeline/jobs/stations.py` + `wrm_pipeline/wrm_pipeline/definitions.py` (S3, job selection + daily schedule), `wrm_pipeline/wrm_pipeline/config.py` + `wrm_pipeline/wrm_pipeline/resources.py` (S4, `WRM_DUCKDB_PATH` / `db_path`), `wrm_pipeline/pyproject.toml` + `uv.lock` (adds `pyarrow>=15`), plus tests `tests/unit/stations/test_processed.py` (+3) and new `tests/unit/test_definitions.py`, `tests/unit/test_config_paths.py`. |

## 10. Environment

| Component | Version |
|---|---|
| uv | 0.10.9 |
| Python | 3.12.3 |
| pytest | 9.1.1 |
| pluggy | 1.6.0 |
| dagster / dagster-aws / dagster-cloud | 1.13.23 / 0.29.23 / 1.13.23 |
| duckdb | 1.5.5 |
| pandas | 3.0.6 |
| **pyarrow** (new dependency, S2) | **25.0.1** |
| OS | Linux 6.8.0-101-generic (x86_64) |

## 11. Results

```bash
uv run pytest   # run 1 → 174 passed, 3 skipped, 15 warnings in 18.89s
uv run pytest   # run 2 → 174 passed, 3 skipped, 15 warnings in 15.64s
```

| Metric | Post-ETL | Pre-ETL | Δ |
|---|---|---|---|
| Collected | 177 | 149 | +28 |
| Passed | **174** | 149 | +25 |
| Failed / Errors | **0 / 0** | 0 / 0 | 0 |
| Skipped | 3 | 0 | +3 (see §14) |
| Warnings | 15 (Pydantic deprecations) | 0 recorded | see §14 |

### Per-file breakdown (177)

| Test file | Tests | Δ vs pre-ETL |
|---|---|---|
| `wrm_pipeline/tests/unit/retry/test_api_retry.py` | 30 | — |
| `wrm_pipeline/tests/unit/retry/test_circuit_breaker.py` | 42 | — |
| `wrm_pipeline/tests/unit/retry/test_config.py` | 8 | — |
| `wrm_pipeline/tests/unit/retry/test_s3_helpers.py` | 21 | — |
| `wrm_pipeline/tests/unit/retry/test_s3_retry.py` | 27 | — |
| `wrm_pipeline/tests/unit/stations/test_processed.py` | 17 | +3 (14 → 17) |
| `wrm_pipeline/tests/unit/stations/test_raw.py` | 7 | — |
| `wrm_pipeline/tests/unit/test_config_paths.py` (new) | 15 | +15 |
| `wrm_pipeline/tests/unit/test_definitions.py` (new) | 10 | +10 |
| **Total** | **177** | **+28** |

Note: `test_config_paths.py` defines 13 test functions; 15 collect because two
are parametrized (remote-URI directory-creation cases).

## 12. Definitions Validation

```bash
uv run dagster definitions validate -m wrm_pipeline.definitions
# → "Validation successful for code location wrm_pipeline.definitions."
# → "All code locations passed validation." (exit 0)
```

`defs.assets` still contains exactly **7 assets** (unchanged from pre-ETL §6).

## 13. Static Grep Checks

1. **Job selection contains the raw asset** — `jobs/stations.py:9-12` selects
   `wrm_stations_raw_data_asset` + `wrm_stations_processed_data_all_asset` +
   `wrm_stations_enhanced_data_all_asset`. PASS.
2. **Daily schedule at 05:00** — `definitions.py:29-33` defines
   `ScheduleDefinition(job=wrm_stations_processing_job, name="daily",
   cron_schedule="0 5 * * *")`, registered in `defs` schedules
   (`definitions.py:71`). PASS.
3. **No `~/data` DuckDB hits** — `grep -rn '\.data\|~/' wrm_pipeline/wrm_pipeline
   --include='*.py' | grep -i duckdb` → **0 matches**. DuckDB path is now the
   repo-root default `db/analytics.duckdb` via `config.py:203-212`
   (`WRM_DUCKDB_PATH` override), consumed by `resources.py:162-193`
   (`database=db_path`). PASS.
   Residual (non-duckdb): `assets/duckdb/bike_spatial_density_analysis.py:218`
   still writes visualizations to `~/data/visualizations` — pre-existing, out
   of S4 duckdb scope, unchanged.

## 14. Skips, Warnings, and Known Risks

- **3 skips, all in `test_config_paths.py:163/170/177`:** reason is
  `dagster-duckdb-pandas not installed; duckdb IO manager is None (optional
  integration degraded gracefully)`. Intentional degradation, not a failure;
  they assert the IO manager is `None` when the optional package is absent.
- **15 warnings (new vs pre-ETL's "none"):** all
  `PydanticDeprecatedSince20` "class-based `config` is deprecated"
  warnings from `wrm_pipeline/wrm_pipeline/vault/models.py` (lines 374–667),
  surfaced by the new tests' import graph pulling in the vault module.
  Non-fatal; would break on Pydantic V3. Not addressed in this step.
- **Dagster CLI supersession:** `dagster definitions validate` emits
  `SupersessionWarning: use 'dg check defs' instead`. Command still works on
  dagster 1.13.23; migrate later.
- **Uncommitted S2–S4 state:** counts reflect HEAD `b2074a3` + uncommitted
  work-tree changes. If the S2–S4 work is committed/amended, re-verify counts
  once against the new commit.
- **Timing variance:** 18.89 s (single S5 run) vs 15.36–16.89 s pre-ETL;
  duration remains advisory only.
- **Empty integration suite** (`tests/integration/`, 0 tests) still a
  placeholder — ETL integration coverage remains a gap.

## 15. Reproducing This Post-ETL Snapshot

```bash
cd /root/programming/bike-data-flow.agent-etl-repair
uv sync --all-packages
uv run pytest                                            # expect: 174 passed, 3 skipped
uv run dagster definitions validate -m wrm_pipeline.definitions
                                                         # expect: all code locations passed
uv run python -c "from wrm_pipeline.definitions import defs; print(len(list(defs.assets)))"
                                                         # expect: 7
grep -rn '\.data\|~/' wrm_pipeline/wrm_pipeline --include='*.py' | grep -i duckdb
                                                         # expect: no output
```
