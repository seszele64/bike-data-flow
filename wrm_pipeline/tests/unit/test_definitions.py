"""Unit tests for the wrm_pipeline Dagster Definitions (code location).

Locks in the current job/schedule wiring:

- ``wrm_stations_processing_job`` is the daily archive job and is
  transform-only: it selects the processed and enhanced assets and must NOT
  include the raw asset (the raw asset is unpartitioned; including it in this
  daily-partitioned job tied an API fetch to every scheduled transform tick).
- ``wrm_stations_ingest_job`` selects the raw station asset only and is
  unpartitioned. It remains registered in the code location's ``Definitions``
  for manual runs (the raw-data sensor still targets the processing job,
  unchanged), but it has no schedule any more.
- ``wrm_stations_latest_refresh_job`` is the 15-minute pipeline: exactly five
  unpartitioned assets/chains (raw → latest processed → latest enhanced →
  DuckDB views → dashboard snapshot). Because it is unpartitioned, its
  schedule ticks emit a bare ``RunRequest`` with no ``partition_key``.
- A schedule named ``latest_refresh`` targets that job every 15 minutes
  (cron ``*/15 * * * *``, UTC).
- The former hourly ``ingest`` schedule (cron ``0 * * * *``) is gone: the
  raw fetch now lives inside the refresh job, so a separate ingest tick would
  duplicate API fetches. Tests assert it stays unregistered so a stale
  deployment fails loudly instead of silently double-fetching.
- A daily schedule named ``daily`` still targets the processing job at 05:00
  (cron ``0 5 * * *``).
- Daily schedule ticks emit a ``RunRequest`` carrying yesterday's (UTC) daily
  ``partition_key``, valid against the job's ``DailyPartitionsDefinition``
  (regression guard for S3-fix1: tagless ticks crash the job's assets, which
  read ``context.partition_key``).
- The ``Definitions`` object imports and loads without errors.

These are pure in-memory assertions: no network, no storage, no Dagster
daemon. Importing ``wrm_pipeline.definitions`` is the heaviest operation and
it only wires objects together (EnvVar-based resources stay unresolved).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dagster import (
    DagsterInvariantViolationError,
    Definitions,
    JobDefinition,
    ScheduleDefinition,
    build_schedule_context,
)

from wrm_pipeline.definitions import defs

JOB_NAME = "wrm_stations_processing_job"
INGEST_JOB_NAME = "wrm_stations_ingest_job"
LATEST_REFRESH_JOB_NAME = "wrm_stations_latest_refresh_job"
SCHEDULE_NAME = "daily"
LATEST_REFRESH_SCHEDULE_NAME = "latest_refresh"
# Removed in L3: the raw fetch moved into the 15-minute refresh job. The name
# must stay unregistered (manual ingest runs target the job directly).
REMOVED_INGEST_SCHEDULE_NAME = "ingest"
# Every 15 minutes: minutes 0/15/30/45 of every hour/day/month/weekday.
LATEST_REFRESH_CRON = "*/15 * * * *"
# 05:00 every day: minute=0, hour=5, every day-of-month/month/weekday.
DAILY_CRON = "0 5 * * *"
RAW_NODE = "wrm_stations_raw_data"
PROCESSED_NODE = "wrm_stations_processed_data_all"
ENHANCED_NODE = "wrm_stations_enhanced_data_all"
PROCESSED_LATEST_NODE = "wrm_stations_processed_data_latest"
ENHANCED_LATEST_NODE = "wrm_stations_enhanced_data_latest"
DUCKDB_VIEWS_NODE = "duckdb_enhanced_views"
DASHBOARD_SNAPSHOT_NODE = "evidence_data_snapshot"
LATEST_REFRESH_NODES = frozenset(
    {
        RAW_NODE,
        PROCESSED_LATEST_NODE,
        ENHANCED_LATEST_NODE,
        DUCKDB_VIEWS_NODE,
        DASHBOARD_SNAPSHOT_NODE,
    }
)

# dagster >= 1.11 emits deprecation UserWarnings when get_job_def /
# get_schedule_def are used against asset-job-backed definitions. These
# accessors are used deliberately here (they are the spec'd API) and still
# resolve correctly on dagster 1.13.23; silence the notices for these tests.
pytestmark = [
    pytest.mark.filterwarnings("ignore:Found asset job named:UserWarning"),
    pytest.mark.filterwarnings(
        "ignore:Starting in dagster 1.11, get_schedule_def:UserWarning"
    ),
    # Emitted by get_job_def("no_such_job") right before it raises.
    pytest.mark.filterwarnings("ignore:JobDefinition with name:UserWarning"),
]


class TestDefinitionsLoad:
    """The code location imports and is internally consistent."""

    def test_import_defs_succeeds(self):
        """`from wrm_pipeline.definitions import defs` works and yields Definitions."""
        assert isinstance(defs, Definitions)

    def test_definitions_are_loadable(self):
        """All assets/jobs/sensors/schedules/resources resolve without errors."""
        # validate_loadable raises on any resolution failure; None == healthy.
        assert Definitions.validate_loadable(defs) is None


class TestProcessingJobIsTransformOnly:
    """wrm_stations_processing_job must exclude the raw asset node (T1-A)."""

    def test_job_is_registered(self):
        job = defs.get_job_def(JOB_NAME)
        assert isinstance(job, JobDefinition)
        assert job.name == JOB_NAME

    def test_job_excludes_raw_node(self):
        """Ingest/transform decoupling: raw materializes via its own job."""
        job = defs.get_job_def(JOB_NAME)
        node_names = set(job.graph.node_names())
        assert RAW_NODE not in node_names

    def test_job_nodes_match_expected_selection(self):
        """Selection is exactly processed + enhanced (no accidental extras)."""
        job = defs.get_job_def(JOB_NAME)
        assert set(job.graph.node_names()) == {
            PROCESSED_NODE,
            ENHANCED_NODE,
        }


class TestIngestJobRegistered:
    """wrm_stations_ingest_job is registered in the code location (T1-A2).

    Resolved through ``defs.get_job_def`` now that the ingest job is
    registered alongside the processing job; previously these assertions
    ran against a direct module import while the job was unregistered.
    """

    def test_ingest_job_is_registered(self):
        job = defs.get_job_def(INGEST_JOB_NAME)
        assert isinstance(job, JobDefinition)
        assert job.name == INGEST_JOB_NAME

    def test_ingest_job_selects_raw_only(self):
        """Selection is exactly the raw asset (no transform nodes)."""
        job = defs.get_job_def(INGEST_JOB_NAME)
        assert set(job.graph.node_names()) == {RAW_NODE}

    def test_ingest_job_is_unpartitioned(self):
        """The raw asset has no partitions_def, so the ingest job has none."""
        job = defs.get_job_def(INGEST_JOB_NAME)
        assert job.partitions_def is None


class TestLatestRefreshJob:
    """wrm_stations_latest_refresh_job is the unpartitioned 15-minute loop (L3)."""

    def test_job_is_registered(self):
        job = defs.get_job_def(LATEST_REFRESH_JOB_NAME)
        assert isinstance(job, JobDefinition)
        assert job.name == LATEST_REFRESH_JOB_NAME

    def test_job_nodes_match_expected_selection(self):
        """Selection is exactly the five pipeline nodes (no accidental extras)."""
        job = defs.get_job_def(LATEST_REFRESH_JOB_NAME)
        assert set(job.graph.node_names()) == LATEST_REFRESH_NODES

    def test_job_has_exactly_five_assets(self):
        """Count is pinned separately so an extra/missing node double-fails."""
        job = defs.get_job_def(LATEST_REFRESH_JOB_NAME)
        assert len(job.graph.node_names()) == len(LATEST_REFRESH_NODES) == 5

    def test_job_includes_raw_fetch_and_dashboard_snapshot(self):
        """Both loop ends are present: API fetch first, dashboard last."""
        job = defs.get_job_def(LATEST_REFRESH_JOB_NAME)
        node_names = set(job.graph.node_names())
        assert {RAW_NODE, DASHBOARD_SNAPSHOT_NODE} <= node_names

    def test_job_excludes_daily_archive_nodes(self):
        """The daily archive assets stay on wrm_stations_processing_job."""
        job = defs.get_job_def(LATEST_REFRESH_JOB_NAME)
        assert set(job.graph.node_names()).isdisjoint({PROCESSED_NODE, ENHANCED_NODE})

    def test_job_is_unpartitioned(self):
        """All selected assets are unpartitioned, so the job has no partitions."""
        job = defs.get_job_def(LATEST_REFRESH_JOB_NAME)
        assert job.partitions_def is None


class TestLatestRefreshSchedule:
    """The 'latest_refresh' schedule runs the refresh job every 15 minutes (L3)."""

    def _evaluate_tick(self, scheduled_execution_time: datetime | None = None):
        """Evaluate one 'latest_refresh' tick and return its single RunRequest."""
        schedule_def = defs.get_schedule_def(LATEST_REFRESH_SCHEDULE_NAME)
        context = build_schedule_context(
            repository_def=defs.get_repository_def(),
            scheduled_execution_time=scheduled_execution_time,
        )
        result = schedule_def.evaluate_tick(context)
        assert len(result.run_requests) == 1
        return result.run_requests[0]

    def test_schedule_exists_and_is_registered(self):
        schedule = defs.get_schedule_def(LATEST_REFRESH_SCHEDULE_NAME)
        assert isinstance(schedule, ScheduleDefinition)
        assert schedule.name == LATEST_REFRESH_SCHEDULE_NAME

    def test_schedule_cron_is_every_15_minutes(self):
        schedule = defs.get_schedule_def(LATEST_REFRESH_SCHEDULE_NAME)
        assert schedule.cron_schedule == LATEST_REFRESH_CRON

    def test_schedule_targets_latest_refresh_job(self):
        schedule = defs.get_schedule_def(LATEST_REFRESH_SCHEDULE_NAME)
        assert schedule.job_name == LATEST_REFRESH_JOB_NAME

    def test_schedule_runs_in_utc(self):
        """execution_timezone is pinned to UTC (matches the daily schedule)."""
        schedule = defs.get_schedule_def(LATEST_REFRESH_SCHEDULE_NAME)
        assert schedule.execution_timezone == "UTC"

    @pytest.mark.parametrize(
        "scheduled_time",
        [
            datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 21, 3, 15, tzinfo=timezone.utc),
            datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc),
            datetime(2026, 9, 21, 3, 45, tzinfo=timezone.utc),
        ],
        ids=["minute-00", "minute-15", "minute-30", "minute-45"],
    )
    def test_tick_emits_single_partitionless_request(self, scheduled_time):
        """Every quarter-hour tick is a bare RunRequest (job is unpartitioned)."""
        run_request = self._evaluate_tick(scheduled_time)
        assert run_request.partition_key is None
        assert "dagster/partition" not in run_request.tags

    def test_tick_without_scheduled_time_emits_partitionless_request(self):
        """Ad-hoc evaluation has no tick time; the tick is still a bare RunRequest."""
        run_request = self._evaluate_tick()
        assert run_request.partition_key is None
        assert "dagster/partition" not in run_request.tags


class TestIngestScheduleRemoved:
    """The former hourly 'ingest' schedule is gone; the ingest job stays manual."""

    def test_ingest_schedule_is_not_registered(self):
        with pytest.raises(DagsterInvariantViolationError, match="ingest"):
            defs.get_schedule_def(REMOVED_INGEST_SCHEDULE_NAME)

    def test_registered_schedules_are_exactly_daily_and_latest_refresh(self):
        registered = {s.name for s in defs.get_repository_def().schedule_defs}
        assert registered == {SCHEDULE_NAME, LATEST_REFRESH_SCHEDULE_NAME}

    def test_no_schedule_targets_the_ingest_job(self):
        """Even if renamed, no remaining schedule may run the raw-only job."""
        schedule_jobs = {
            s.job_name for s in defs.get_repository_def().schedule_defs
        }
        assert INGEST_JOB_NAME not in schedule_jobs


class TestDailySchedule:
    """A 'daily' schedule runs the stations job at 05:00 every day."""

    def test_schedule_exists_and_is_registered(self):
        schedule = defs.get_schedule_def(SCHEDULE_NAME)
        assert isinstance(schedule, ScheduleDefinition)
        assert schedule.name == SCHEDULE_NAME

    def test_schedule_cron_is_daily_at_5am(self):
        schedule = defs.get_schedule_def(SCHEDULE_NAME)
        assert schedule.cron_schedule == DAILY_CRON

    def test_schedule_targets_stations_job(self):
        schedule = defs.get_schedule_def(SCHEDULE_NAME)
        assert schedule.job_name == JOB_NAME

    def test_schedule_runs_in_utc(self):
        """execution_timezone is pinned to UTC (S3-fix1 contract)."""
        schedule = defs.get_schedule_def(SCHEDULE_NAME)
        assert schedule.execution_timezone == "UTC"


class TestDailyScheduleTickPartitionKey:
    """S3-fix1: a schedule tick must emit a RunRequest with a valid partition key.

    The schedule computes yesterday (UTC) from the tick's scheduled execution
    time and returns it as ``partition_key``. The job's partitioned assets read
    ``context.partition_key``, which raises on tagless runs, so every tick must
    carry a key that resolves against the job's daily partitions
    (``DailyPartitionsDefinition(start_date="2025-05-01")``, UTC, end_offset 0).
    """

    def _evaluate_tick(self, scheduled_execution_time: datetime | None = None):
        """Evaluate one 'daily' schedule tick and return its single RunRequest."""
        schedule_def = defs.get_schedule_def(SCHEDULE_NAME)
        context = build_schedule_context(
            repository_def=defs.get_repository_def(),
            scheduled_execution_time=scheduled_execution_time,
        )
        result = schedule_def.evaluate_tick(context)
        assert len(result.run_requests) == 1
        return result.run_requests[0]

    def test_tick_returns_yesterdays_partition_key(self):
        """Tick at 2026-09-20T05:00Z → RunRequest for partition 2026-09-19."""
        run_request = self._evaluate_tick(
            datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)
        )
        assert run_request.partition_key == "2026-09-19"

    def test_tick_partition_key_is_valid_for_daily_partitions(self):
        """The emitted key resolves against the job's daily partitions."""
        run_request = self._evaluate_tick(
            datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)
        )
        partitions_def = defs.get_job_def(JOB_NAME).partitions_def
        assert partitions_def.has_partition_key(run_request.partition_key)

    def test_tick_resolves_dagster_partition_tag(self):
        """evaluate_tick resolves the dagster/partition tag the launch reads."""
        run_request = self._evaluate_tick(
            datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)
        )
        assert run_request.tags["dagster/partition"] == "2026-09-19"

    def test_tick_converts_non_utc_scheduled_time_to_utc(self):
        """07:00+02:00 == 05:00 UTC → same partition (cron is UTC-pinned)."""
        plus_two = timezone(timedelta(hours=2))
        run_request = self._evaluate_tick(datetime(2026, 9, 20, 7, 0, tzinfo=plus_two))
        assert run_request.partition_key == "2026-09-19"

    def test_tick_without_scheduled_time_falls_back_to_current_yesterday(self):
        """Ad-hoc evaluation has no tick time; the schedule uses now(UTC).

        Bracketed assertion tolerates a UTC-midnight rollover between the
        schedule's internal ``now()`` and this test's ``now()``.
        """
        before = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        run_request = self._evaluate_tick()
        after = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        assert run_request.partition_key in {before, after}
        assert defs.get_job_def(JOB_NAME).partitions_def.has_partition_key(
            run_request.partition_key
        )

    def test_tick_on_partition_start_date_yields_valid_key(self):
        """Day-one tick is clamped to the first valid key (not 2025-04-30).

        The schedule derives yesterday (2025-04-30) for a tick on the
        partitions' start_date, which is outside the range; it must clamp to
        the first partition key instead of emitting an unknown key, and the
        RunRequest must carry the dagster/partition tag so the launched run
        resolves ``context.partition_key``.
        """
        run_request = self._evaluate_tick(
            datetime(2025, 5, 1, 5, 0, tzinfo=timezone.utc)
        )
        assert run_request.partition_key == "2025-05-01"
        assert run_request.tags["dagster/partition"] == "2025-05-01"
        partitions_def = defs.get_job_def(JOB_NAME).partitions_def
        assert partitions_def.has_partition_key(run_request.partition_key)

    def test_tick_day_after_start_date_yields_first_key(self):
        """The day after start_date targets yesterday, the first partition."""
        run_request = self._evaluate_tick(
            datetime(2025, 5, 2, 5, 0, tzinfo=timezone.utc)
        )
        assert run_request.partition_key == "2025-05-01"
        partitions_def = defs.get_job_def(JOB_NAME).partitions_def
        assert partitions_def.has_partition_key(run_request.partition_key)


class TestLookupBoundaries:
    """Edge cases: lookups for names that are not registered must fail loudly."""

    def test_unknown_job_name_raises(self):
        with pytest.raises(DagsterInvariantViolationError, match="no_such_job"):
            defs.get_job_def("no_such_job")

    def test_unknown_schedule_name_raises(self):
        with pytest.raises(DagsterInvariantViolationError, match="no_such_schedule"):
            defs.get_schedule_def("no_such_schedule")
