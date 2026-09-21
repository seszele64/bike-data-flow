"""Unit tests for the stations raw-data sensor (T1-A3).

Locks in the transform-only, loop-safe trigger contract:

- The sensor targets ``wrm_stations_processing_job`` (processed + enhanced
  assets only), never the raw/ingest side.
- It is throttled to at least 5 minutes between ticks and its default status
  is STOPPED (it must not auto-start with the code location).
- Adopt-on-first-tick: with no cursor the sensor records the newest raw file
  timestamp and emits no runs (enabling the sensor must not replay history).
- Later ticks emit one RunRequest per affected partition, each with a
  per-partition ``run_key`` of ``"<partition>:<batch latest ISO>"`` that is
  stable across re-evaluation of the same batch (so Dagster deduplicates
  re-ticks instead of launching duplicate runs).
- An empty raw state yields a SkipReason.

All evaluations run against a fake S3 resource via ``evaluate_tick``; the
repository definition needed for run-request resolution is built in-memory
from the transform job's own assets, so no S3, network, or daemon is touched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

from dagster import (
    DefaultSensorStatus,
    Definitions,
    ResourceDefinition,
    build_sensor_context,
)

from wrm_pipeline.assets.stations.enhanced_all import (
    wrm_stations_enhanced_data_all_asset,
)
from wrm_pipeline.assets.stations.processed_all import (
    wrm_stations_processed_data_all_asset,
)
from wrm_pipeline.jobs.stations import wrm_stations_processing_job
from wrm_pipeline.sensors.stations import wrm_stations_raw_data_sensor

PROCESSING_JOB_NAME = "wrm_stations_processing_job"
RAW_PREFIX = "bike-data/gen_info/raw/"
OLDER_TS = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
LATEST_TS = datetime(2026, 9, 20, 10, 30, tzinfo=timezone.utc)
LATEST_ISO = "2026-09-20T10:30:00"
OLD_CURSOR = "2026-09-18T00:00:00"

# (key, LastModified) pairs mimicking S3 list_objects_v2 Contents entries.
OLD_FILE = (f"{RAW_PREFIX}dt=2026-09-19/old.txt", OLDER_TS)
NEW_FILE_P19 = (f"{RAW_PREFIX}dt=2026-09-19/new.txt", LATEST_TS)
NEW_FILE_P20 = (f"{RAW_PREFIX}dt=2026-09-20/new.txt", LATEST_TS)

# In-memory repository so evaluate_tick can resolve partitioned RunRequests
# against the transform job without importing wrm_pipeline.definitions.
REPO_DEF = Definitions(
    assets=[wrm_stations_processed_data_all_asset, wrm_stations_enhanced_data_all_asset],
    jobs=[wrm_stations_processing_job],
    resources={"s3_resource": ResourceDefinition.mock_resource()},
).get_repository_def()


def _evaluate(entries, cursor=None):
    """Evaluate one sensor tick against a fake S3 listing of (key, ts) pairs."""
    fake_s3 = Mock(name="s3_resource")
    fake_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": key, "LastModified": ts} for key, ts in entries]
    }
    context = build_sensor_context(
        cursor=cursor,
        resources={"s3_resource": fake_s3},
        repository_def=REPO_DEF,
    )
    return wrm_stations_raw_data_sensor.evaluate_tick(context)


def _skip_message(result):
    """Skip message of a tick result; None when the tick emitted runs.

    A bare ``SkipReason`` return is surfaced either directly or wrapped in
    ``SensorExecutionData(skip_message=...)`` by evaluate_tick, depending on
    dagster version; both carry the message under ``skip_message``.
    """
    return getattr(result, "skip_message", None)


class TestSensorContract:
    """Static wiring: throttled, STOPPED by default, transform-only target."""

    def test_minimum_interval_is_at_least_five_minutes(self):
        assert wrm_stations_raw_data_sensor.minimum_interval_seconds >= 300

    def test_default_status_is_stopped(self):
        assert wrm_stations_raw_data_sensor.default_status == DefaultSensorStatus.STOPPED

    def test_targets_transform_only_processing_job(self):
        assert [job.name for job in wrm_stations_raw_data_sensor.jobs] == [
            PROCESSING_JOB_NAME
        ]


class TestFirstTickAdoptsState:
    """Cursor-less ticks adopt the current raw state and emit nothing."""

    def test_adopt_no_emit_sets_cursor_to_latest(self):
        result = _evaluate([OLD_FILE, NEW_FILE_P20], cursor=None)
        assert result.run_requests == []
        assert result.cursor == LATEST_ISO

    def test_corrupt_cursor_is_adopted_without_emitting(self):
        result = _evaluate([OLD_FILE, NEW_FILE_P20], cursor="not-a-timestamp")
        assert result.run_requests == []
        assert result.cursor == LATEST_ISO


class TestNewDataEmitsPerPartition:
    """Ticks after adoption emit one run per partition touched by new files."""

    def test_one_run_request_per_partition(self):
        result = _evaluate([OLD_FILE, NEW_FILE_P19, NEW_FILE_P20], cursor=OLD_CURSOR)
        assert sorted(rr.partition_key for rr in result.run_requests) == [
            "2026-09-19",
            "2026-09-20",
        ]

    def test_run_key_is_partition_plus_batch_latest(self):
        result = _evaluate([OLD_FILE, NEW_FILE_P19, NEW_FILE_P20], cursor=OLD_CURSOR)
        for run_request in result.run_requests:
            assert run_request.run_key == f"{run_request.partition_key}:{LATEST_ISO}"

    def test_cursor_advances_to_batch_latest(self):
        result = _evaluate([OLD_FILE, NEW_FILE_P19, NEW_FILE_P20], cursor=OLD_CURSOR)
        assert result.cursor == LATEST_ISO


class TestRunKeyStability:
    """Re-evaluating the same batch reproduces identical run keys."""

    def test_same_batch_reproduces_identical_run_keys(self):
        first = _evaluate([OLD_FILE, NEW_FILE_P19, NEW_FILE_P20], cursor=OLD_CURSOR)
        second = _evaluate([OLD_FILE, NEW_FILE_P19, NEW_FILE_P20], cursor=OLD_CURSOR)
        keys_first = [rr.run_key for rr in first.run_requests]
        keys_second = [rr.run_key for rr in second.run_requests]
        assert keys_first == keys_second
        # Keys are unique per partition so dedupe never collapses two partitions.
        assert len(set(keys_first)) == len(keys_first)


class TestSkipReasons:
    """Nothing new to process must surface as a skip, not a run."""

    def test_no_raw_files_skips(self):
        result = _evaluate([], cursor=None)
        assert result.run_requests == []
        assert _skip_message(result) == "No raw data files found"

    def test_no_txt_files_skips(self):
        result = _evaluate(
            [(f"{RAW_PREFIX}dt=2026-09-19/notes.csv", LATEST_TS)], cursor=OLD_CURSOR
        )
        assert result.run_requests == []
        assert _skip_message(result) == "No .txt files found in raw data"

    def test_no_new_files_since_cursor_skips(self):
        result = _evaluate([OLD_FILE, NEW_FILE_P20], cursor=LATEST_ISO)
        assert result.run_requests == []
        assert _skip_message(result) == "No new raw data files found"
