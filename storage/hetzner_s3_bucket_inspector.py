import boto3
import os
from dotenv import load_dotenv

# Replace with your Hetzner Object Storage credentials and endpoint
# Load environment variables from .env file
load_dotenv()

# Get environment variables
HETZNER_ENDPOINT_URL = os.getenv("HETZNER_ENDPOINT_URL")
HETZNER_ACCESS_KEY_ID = os.getenv("HETZNER_ACCESS_KEY_ID")
HETZNER_SECRET_ACCESS_KEY = os.getenv("HETZNER_SECRET_ACCESS_KEY")
BUCKET_NAME = os.getenv("BUCKET_NAME")
TARGET_S3_FOLDER = os.getenv("TARGET_S3_FOLDER", "")  # Include this if needed, with default empty string

# Ensure the endpoint URL has a scheme
if HETZNER_ENDPOINT_URL and not HETZNER_ENDPOINT_URL.startswith(('http://', 'https://')):
    HETZNER_ENDPOINT_URL = 'https://' + HETZNER_ENDPOINT_URL


def test_hetzner_connection_and_list_bucket_structure():
    """
    Tests the connection to Hetzner Object Storage and lists the structure of a given bucket.
    """
    print(f"Attempting to connect to Hetzner Object Storage endpoint: {HETZNER_ENDPOINT_URL}")
    print(f"Using Access Key ID: {HETZNER_ACCESS_KEY_ID[:5]}... (partially hidden)") # Avoid printing full key

    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=HETZNER_ENDPOINT_URL,
            aws_access_key_id=HETZNER_ACCESS_KEY_ID,
            aws_secret_access_key=HETZNER_SECRET_ACCESS_KEY
        )
        print("Successfully created S3 client.")
    except Exception as e:
        print(f"Error creating S3 client: {e}")
        return

    # 1. Test connection by listing buckets (optional, but good for diagnostics)
    try:
        response = s3_client.list_buckets()
        print("\nSuccessfully connected. Available buckets:")
        bucket_found = False
        for bucket in response['Buckets']:
            print(f"  - {bucket['Name']}")
            if bucket['Name'] == BUCKET_NAME:
                bucket_found = True
        if not bucket_found:
            print(f"\nWarning: Bucket '{BUCKET_NAME}' not found in the list of all buckets.")
            print("Please ensure the bucket name is correct and exists.")
            # You might want to exit here if the specified bucket must exist
            # return
        elif not response['Buckets']:
            print("No buckets found for this account.")

    except Exception as e:
        print(f"Error listing buckets (connection/authentication issue likely): {e}")
        print("Please check your endpoint URL, access key, secret key, and network connectivity.")
        return

    # 2. List objects in the specified bucket
    if not BUCKET_NAME:
        print("\nBUCKET_NAME is not set. Cannot list bucket contents.")
        return

    print(f"\nListing objects in bucket: '{BUCKET_NAME}'...")
    try:
        # Using paginator for buckets with many objects
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=BUCKET_NAME)

        object_count = 0
        for page in page_iterator:
            if "Contents" in page:
                for obj in page["Contents"]:
                    object_count += 1
                    print(f"  - {obj['Key']} (Size: {obj['Size']} bytes, LastModified: {obj['LastModified']})")
            else:
                # This can happen if the bucket is empty or if there are no more objects
                pass
        
        if object_count == 0:
            print(f"No objects found in bucket '{BUCKET_NAME}'.")
        else:
            print(f"\nFound {object_count} object(s) in '{BUCKET_NAME}'.")

    except s3_client.exceptions.NoSuchBucket:
        print(f"Error: The bucket '{BUCKET_NAME}' does not exist.")
    except Exception as e:
        print(f"Error listing objects in bucket '{BUCKET_NAME}': {e}")

if __name__ == "__main__":
    if not all([HETZNER_ENDPOINT_URL, HETZNER_ACCESS_KEY_ID, HETZNER_SECRET_ACCESS_KEY, BUCKET_NAME]):
        print("One or more required environment variables (HETZNER_ENDPOINT_URL, HETZNER_ACCESS_KEY_ID, HETZNER_SECRET_ACCESS_KEY, BUCKET_NAME) are not set.")
        print("You can also hardcode them in the script, but environment variables are recommended.")
    else:
        test_hetzner_connection_and_list_bucket_structure()