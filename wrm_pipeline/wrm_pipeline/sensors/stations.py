from datetime import datetime
import re

from dagster import sensor, SensorResult, RunRequest, SkipReason, SensorEvaluationContext
from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from ..jobs.stations import wrm_stations_processing_job

@sensor(
    name="wrm_stations_raw_data_sensor",
    job=wrm_stations_processing_job,
    minimum_interval_seconds=300,  # At most one tick every 5 minutes
    required_resource_keys={"s3_resource"}  # Add this
)
def wrm_stations_raw_data_sensor(context: SensorEvaluationContext):
    """Trigger the transform-only stations processing job on new raw data.

    Loop-safety contract (T1-A3):

    - Transform-only: the target is ``wrm_stations_processing_job``
      (processed + enhanced assets only), so sensor-launched runs never
      re-fetch the WRM API; raw ingestion is owned by the ingest job.
    - Adopt-on-first-tick: with no cursor (or an unparsable cursor) the
      sensor adopts the current raw state — it advances its cursor to the
      newest file timestamp and emits no runs — so enabling the sensor never
      replays historical data as a burst of runs.
    - Idempotent run keys: each emitted run's ``run_key`` is
      ``"<partition>:<batch latest ISO timestamp>"``. Re-evaluating the same
      batch (e.g. a daemon crash between run launch and cursor persist)
      produces the same run keys, which Dagster deduplicates.
    - Throttled: ticks are spaced at least 5 minutes apart.
    """

    # Use the same S3 resource as your assets
    s3_client = context.resources.s3_resource

    try:
        # Get the cursor (last processed file timestamp)
        cursor = context.cursor
        last_processed_timestamp = None
        if cursor:
            try:
                last_processed_timestamp = datetime.fromisoformat(cursor)
                context.log.info(f"Last processed timestamp: {last_processed_timestamp}")
            except ValueError:
                context.log.warning(f"Invalid cursor format: {cursor}")

        # List all raw data files
        raw_s3_prefix = f"{WRM_STATIONS_S3_PREFIX}raw/"

        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=raw_s3_prefix
        )

        if 'Contents' not in response or not response['Contents']:
            return SkipReason("No raw data files found")

        # Filter to only include .txt files and sort by LastModified
        txt_files = [obj for obj in response['Contents'] if obj['Key'].endswith('.txt')]

        if not txt_files:
            return SkipReason("No .txt files found in raw data")

        # Sort files by LastModified
        txt_files.sort(key=lambda x: x['LastModified'])

        # Find new files since last processed timestamp
        new_files = []
        latest_timestamp = None

        for file_obj in txt_files:
            file_timestamp = file_obj['LastModified'].replace(tzinfo=None)

            if last_processed_timestamp is None or file_timestamp > last_processed_timestamp:
                new_files.append(file_obj)

            # Track the latest timestamp
            if latest_timestamp is None or file_timestamp > latest_timestamp:
                latest_timestamp = file_timestamp

        # Adopt-on-first-tick: without a usable cursor there is no baseline,
        # so record one and emit nothing. A freshly enabled sensor (or one
        # recovering from a corrupt cursor) must not storm the job with runs
        # for historical data.
        if last_processed_timestamp is None:
            adopted_cursor = latest_timestamp.isoformat()
            context.log.info(
                f"No usable cursor; adopting current raw state up to "
                f"{adopted_cursor} without emitting runs"
            )
            return SensorResult(run_requests=[], cursor=adopted_cursor)

        if not new_files:
            return SkipReason("No new raw data files found")

        context.log.info(f"Found {len(new_files)} new raw data files")

        # Group files by date partition to create separate runs
        date_partitions = {}
        for file_obj in new_files:
            # Extract date from S3 key (format: raw/dt=YYYY-MM-DD/...)
            date_match = re.search(r'dt=(\d{4}-\d{2}-\d{2})', file_obj['Key'])
            if date_match:
                partition_date = date_match.group(1)
                if partition_date not in date_partitions:
                    date_partitions[partition_date] = []
                date_partitions[partition_date].append(file_obj)

        # Latest timestamp of this batch; also becomes the new cursor value.
        batch_latest_iso = latest_timestamp.isoformat() if latest_timestamp else None

        # Create run requests for each date partition. The run_key pins the
        # batch's latest timestamp so a re-tick of the same batch is
        # deduplicated by Dagster instead of launching duplicate runs.
        run_requests = []
        for partition_date, files in date_partitions.items():
            run_requests.append(
                RunRequest(
                    run_key=f"{partition_date}:{batch_latest_iso}",
                    partition_key=partition_date,
                    tags={
                        "source": "wrm_stations_raw_data_sensor",
                        "partition_date": partition_date,
                        "new_files_count": str(len(files))
                    }
                )
            )

            context.log.info(f"Creating run request for partition {partition_date} with {len(files)} new files")

        # Update cursor to latest timestamp
        return SensorResult(
            run_requests=run_requests,
            cursor=batch_latest_iso
        )

    except Exception as e:
        context.log.error(f"Sensor evaluation failed: {e}")
        return SkipReason(f"Sensor evaluation failed: {e}")
