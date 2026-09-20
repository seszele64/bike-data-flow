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
from .jobs.stations import wrm_stations_processing_job
from .vault import vault_secrets_resource

# Load environment variables from .env file in parent directory
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
load_dotenv(dotenv_path)

all_assets = load_assets_from_modules([assets])


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
    return RunRequest(partition_key=partition_key)


defs = Definitions(
    assets=all_assets,
    jobs=[
        wrm_stations_processing_job,
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
    ]
)
