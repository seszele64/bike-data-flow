"""Unit tests for the wrm_pipeline Dagster Definitions (code location).

Locks in the S3-step contract:

- ``wrm_stations_processing_job`` selects the raw station asset in addition
  to the processed and enhanced assets (regression guard: the raw asset was
  previously missing from the job selection).
- A daily schedule named ``daily`` targets that job at 05:00
  (cron ``0 5 * * *``).
- Schedule ticks emit a ``RunRequest`` carrying yesterday's (UTC) daily
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
SCHEDULE_NAME = "daily"
# 05:00 every day: minute=0, hour=5, every day-of-month/month/weekday.
DAILY_CRON = "0 5 * * *"
RAW_NODE = "wrm_stations_raw_data"
PROCESSED_NODE = "wrm_stations_processed_data_all"
ENHANCED_NODE = "wrm_stations_enhanced_data_all"

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


class TestJobSelectsRawAsset:
    """wrm_stations_processing_job must include the raw asset node."""

    def test_job_is_registered(self):
        job = defs.get_job_def(JOB_NAME)
        assert isinstance(job, JobDefinition)
        assert job.name == JOB_NAME

    def test_job_includes_raw_node(self):
        """Regression guard for the S3 change (jobs/stations.py raw selection)."""
        job = defs.get_job_def(JOB_NAME)
        node_names = set(job.graph.node_names())
        assert RAW_NODE in node_names

    def test_job_nodes_match_expected_selection(self):
        """Selection is exactly raw + processed + enhanced (no accidental extras)."""
        job = defs.get_job_def(JOB_NAME)
        assert set(job.graph.node_names()) == {
            RAW_NODE,
            PROCESSED_NODE,
            ENHANCED_NODE,
        }


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

    @pytest.mark.xfail(
        reason="Known boundary: a tick on the partitions' start_date itself "
        "(2025-05-01T05:00Z) derives key 2025-04-30, before "
        "DailyPartitionsDefinition(start_date='2025-05-01'), and evaluate_tick "
        "raises DagsterUnknownPartitionError while resolving partition tags. "
        "Unreachable in production (deployed 2026); will XPASS if the schedule "
        "ever clamps to the first valid partition.",
        strict=True,
    )
    def test_tick_on_partition_start_date_yields_valid_key(self):
        """Day-one tick must produce a key inside the partitions' range."""
        run_request = self._evaluate_tick(
            datetime(2025, 5, 1, 5, 0, tzinfo=timezone.utc)
        )
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
