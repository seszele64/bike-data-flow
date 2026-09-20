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
