import pandas as pd
from dagster import Definitions, asset, define_asset_job # Import define_asset_job
from dagster_aws.s3 import S3Resource

@asset
def hetzner_s3_asset(s3: S3Resource):
    # Example dataframe to upload
    df = pd.DataFrame({"column1": [1, 2, 3], "column2": ["A", "B", "C"]})
    csv_data = df.to_csv(index=False)
    
    # Get S3 client
    s3_client = s3.get_client()
    
    # Upload to Hetzner Object Storage
    s3_client.put_object(
        Bucket="disband-yodel-botanical",
        Key="bike-data/my_dataframe.csv",
        Body=csv_data,
    )
    
    return "Successfully uploaded data to Hetzner Object Storage"

# Configure S3Resource to use Hetzner instead of AWS
hetzner_s3 = S3Resource(
    endpoint_url="https://nbg1.your-objectstorage.com",
    aws_access_key_id="2C4LYNWPV5EEENFZSF3V",
    aws_secret_access_key="wAOupzMrHtOBfp54F5qlHa5X4VYr2Otr2Vwmwsvl",
)

# Create a job definition from the asset
hetzner_s3_asset_execution_job = define_asset_job(
    name="hetzner_s3_asset_execution_job", 
    selection=[hetzner_s3_asset]
)

defs = Definitions(
    assets=[hetzner_s3_asset],
    resources={"s3": hetzner_s3},
    jobs=[hetzner_s3_asset_execution_job] # Add the job to the Definitions
)

if __name__ == "__main__":
    from dagster import DagsterInstance

    # For local script execution, an ephemeral instance is suitable.
    instance = DagsterInstance.ephemeral()

    # Get the fully resolved job definition from the Definitions object
    job_to_execute = defs.get_job_def("hetzner_s3_asset_execution_job")

    # Execute the job in-process.
    # The job will use the resources configured in the 'defs' object.
    result = job_to_execute.execute_in_process(
        instance=instance
    )

    if result.success:
        print("Job executed successfully")
    else:
        print("Job failed")
        if result.failure_data:
            print(f"Error: {result.failure_data.error}")