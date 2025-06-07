from dagster import sensor, RunRequest, SkipReason, SensorEvaluationContext
from dagster_aws.s3.resources import S3Resource
from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

@sensor(
    asset_selection=["wrm_stations_processed"],
    minimum_interval_seconds=60,  # Check every minute
    name="s3_raw_stations_sensor",
    required_resource_keys={"s3_resource"}  # Declare the required resource
)
def s3_raw_stations_sensor(context: SensorEvaluationContext):
    """
    Sensor that monitors S3 for new raw station files and triggers processing.
    """
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # Define the S3 path to monitor (raw files)
    s3_prefix = f"{WRM_STATIONS_S3_PREFIX}raw/"
    
    # Get the last processed key from cursor
    last_processed_key = context.cursor or None
    
    try:
        # List objects in the S3 bucket with the specified prefix
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=s3_prefix
        )
        
        # Get all keys
        all_keys = []
        if 'Contents' in response:
            all_keys = [obj['Key'] for obj in response['Contents']]
        
        # Filter for .txt files only and newer than cursor
        txt_files = []
        for key in all_keys:
            if key.endswith('.txt'):
                if last_processed_key is None or key > last_processed_key:
                    txt_files.append(key)
        
        if not txt_files:
            return SkipReason("No new raw station files found in S3")
        
        # Sort files to process in order
        txt_files.sort()
        
        # Create run requests for each new file
        for s3_key in txt_files:
            yield RunRequest(
                run_key=s3_key,  # Use S3 key as unique run identifier
                tags={
                    "s3_bucket": BUCKET_NAME,
                    "s3_key": s3_key,
                    "sensor_name": "s3_raw_stations_sensor"
                }
            )
        
        # Update cursor to the latest processed key
        if txt_files:
            context.update_cursor(txt_files[-1])
            context.log.info(f"Triggered processing for {len(txt_files)} new files")
    
    except Exception as e:
        context.log.error(f"Error checking S3 for new files: {e}")
        return SkipReason(f"Error checking S3: {str(e)}")