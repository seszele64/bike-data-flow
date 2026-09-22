from datetime import datetime, timedelta, timezone

from dagster import (
    Definitions,
    EnvVar,
    RunRequest,
    ScheduleEvaluationContext,
    load_assets_from_modules,
    schedule,
)
import os
from dotenv import load_dotenv

from .assets import assets
from .assets.stations.commons import daily_partitions
from .resources import (
    s3_resource,
    postgres_resource,
    duckdb_io_manager,
    duckdb_s3_io_manager,
    duckdb_hybrid_io_manager,
    s3_io_manager,
    hive_partitioned_s3_io_manager
)
from .sensors.stations import wrm_stations_raw_data_sensor
from .jobs.stations import wrm_stations_ingest_job, wrm_stations_processing_job
from .vault import vault_secrets_resource

# Load environment variables from .env file in parent directory
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
load_dotenv(dotenv_path)

all_assets = load_assets_from_modules([assets])

# Tag Dagster uses to carry a run's partition key (dagster._core.storage.tags
# .PARTITION_NAME_TAG — not re-exported from the public `dagster` namespace;
# the value is storage-stable and asserted in tests/unit/test_definitions.py).
PARTITION_TAG = "dagster/partition"


# Daily schedule for the stations processing job (05:00 every day, UTC).
# Note: build_schedule_from_partitioned_job() derives its cron from the job's
# DailyPartitionsDefinition and rejects a custom cron_schedule for
# time-partitioned jobs, so a custom @schedule is used to run at 05:00 and
# explicitly target the previous day's partition. The RunRequest must carry a
# partition_key: the job's assets read context.partition_key, which raises on
# tagless runs (see sensors/stations.py for the event-driven equivalent).
@schedule(
    job=wrm_stations_processing_job,
    name="daily",
    cron_schedule="0 5 * * *",
    execution_timezone="UTC",
)
def wrm_stations_daily_schedule(context: ScheduleEvaluationContext) -> RunRequest:
    """Run the stations processing job for yesterday's daily partition."""
    # dagster 1.13: scheduled_execution_time raises (instead of returning None)
    # when the context has no tick time (e.g. ad-hoc evaluation); fall back to
    # the current UTC time.
    try:
        scheduled_time = context.scheduled_execution_time
    except Exception:
        scheduled_time = datetime.now(timezone.utc)
    partition_key = (
        scheduled_time.astimezone(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    # Zero-padded ISO date keys compare correctly as strings. Clamp to the
    # partitions' first key: a tick on the partitions' start_date itself
    # (2025-05-01T05:00Z) derives 2025-04-30, before
    # DailyPartitionsDefinition(start_date="2025-05-01").
    first_key = daily_partitions.get_first_partition_key()
    if first_key and partition_key < first_key:
        partition_key = first_key
        # On the partitions' first day the first daily window has not elapsed
        # yet (end_offset=0 validates only elapsed windows), so Dagster finds
        # NO valid key at this tick and evaluate_tick raises
        # DagsterUnknownPartitionError for any partition_key. Pre-setting the
        # partition tag marks the request as already resolved
        # (RunRequest.has_resolved_partition), so evaluate_tick keeps the
        # clamped key instead of validating it against tick time; the launched
        # run carries the same dagster/partition tag resolution would set.
        return RunRequest(partition_key=partition_key, tags={PARTITION_TAG: partition_key})
    return RunRequest(partition_key=partition_key)


# Hourly ingest schedule (T1-A2): runs the raw-only, unpartitioned ingest
# job at minute 0 of every hour (UTC). The ingest job has no partitions_def,
# so its ticks emit a bare RunRequest without a partition_key — unlike the
# daily transform schedule above, whose partitioned assets require one.
INGEST_CRON_SCHEDULE = "0 * * * *"


@schedule(
    job=wrm_stations_ingest_job,
    name="ingest",
    cron_schedule=INGEST_CRON_SCHEDULE,
    execution_timezone="UTC",
)
def wrm_stations_ingest_schedule(context: ScheduleEvaluationContext) -> RunRequest:
    """Fetch the latest raw WRM station snapshot from the API every hour."""
    return RunRequest()


defs = Definitions(
    assets=all_assets,
    jobs=[
        wrm_stations_processing_job,
        wrm_stations_ingest_job,
    ],
    sensors=[
        wrm_stations_raw_data_sensor,
    ],
    resources={
        key: resource
        for key, resource in {
            "s3_resource": s3_resource,
            "s3": s3_resource,  # Add this - s3_io_manager expects key "s3"
            "postgres_resource": postgres_resource,
            "duckdb_io_manager": duckdb_io_manager,
            "duckdb_s3_io_manager": duckdb_s3_io_manager,
            "duckdb_hybrid_io_manager": duckdb_hybrid_io_manager,
            "s3_io_manager": s3_io_manager,
            "hive_partitioned_s3_io_manager": hive_partitioned_s3_io_manager,
            "vault": vault_secrets_resource().configured(
                {
                    "vault_addr": EnvVar("VAULT_ADDR"),
                    "auth_method": EnvVar("VAULT_AUTH_METHOD"),
                    "role_id": EnvVar("VAULT_ROLE_ID"),
                    "secret_id": EnvVar("VAULT_SECRET_ID"),
                    "timeout": 30,
                    "retries": 3,
                    "cache_ttl": 300,
                    "verify": True,
                }
            ),
        }.items()
        # Skip resources backed by integrations that are not installed
        if resource is not None
    },
    schedules=[
        wrm_stations_daily_schedule,
        wrm_stations_ingest_schedule,
    ]
)
