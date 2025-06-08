from dagster import sensor, SkipReason, SensorEvaluationContext, RunRequest, define_asset_job
from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

# Define asset job for the WRM stations pipeline
wrm_stations_job = define_asset_job(
    name="wrm_stations_etl_job",
    selection="wrm_stations_postgres"  # This will include all upstream dependencies
)

@sensor(
    minimum_interval_seconds=120,  # Check every 2 minutes
    name="s3_processed_stations_sensor",
    required_resource_keys={"s3_resource"},
    job=wrm_stations_job
)
def s3_processed_stations_sensor(context: SensorEvaluationContext):
    """
    Sensor that monitors S3 for new processed station files and triggers asset materialization.
    """
    
    s3_client = context.resources.s3_resource
    s3_prefix = f"{WRM_STATIONS_S3_PREFIX}processed/"
    
    # Get the last processed key from cursor
    last_processed_key = context.cursor or None
    
    try:
        # List objects in the S3 bucket with the specified prefix
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=s3_prefix
        )
        
        # Get parquet files newer than cursor
        new_files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                if key.endswith('.parquet'):
                    if last_processed_key is None or key > last_processed_key:
                        new_files.append(key)
        
        if not new_files:
            return SkipReason("No new processed station files found in S3")
        
        # Sort files to get the latest
        new_files.sort()
        latest_file = new_files[-1]
        
        # Update cursor to the latest file
        context.update_cursor(latest_file)
        
        # Trigger asset materialization
        context.log.info(f"Found {len(new_files)} new files. Latest: {latest_file}")
        
        return RunRequest(
            run_key=f"wrm_stations_{latest_file.split('/')[-1].replace('.parquet', '')}",
            tags={
                "source": "s3_sensor",
                "latest_file": latest_file,
                "new_files_count": str(len(new_files))
            }
        )
    
    except Exception as e:
        context.log.error(f"Error checking S3 for processed files: {e}")
        return SkipReason(f"Error checking S3: {str(e)}")