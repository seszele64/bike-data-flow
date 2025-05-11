#!/usr/bin/env python3

import os
import requests
from datetime import datetime
from minio.error import S3Error
from io import BytesIO

# Import configuration
from .. import config
from ...create_client import get_minio_client

def main():
    # Generate filename with current date
    current_date_str = datetime.now().strftime('%Y-%m-%d')
    filename = f"{config.WRM_FAILURES_BASE_FILENAME}_{current_date_str}.{config.CSV_EXTENSION}"
    s3_object_name = f"{config.WRM_FAILURES_TARGET_FOLDER}{filename}"

    # Check for necessary configuration
    if not config.BUCKET_NAME:
        print("Error: BUCKET_NAME is missing in environment variables.")
        return

    # Get MinIO client using the centralized function
    minio_client = get_minio_client(dotenv_path=config.ENV_PATH)
    if not minio_client:
        return

    print(f"Attempting to download data from: {config.WRM_FAILURES_DATA_URL}")
    try:
        response = requests.get(config.WRM_FAILURES_DATA_URL, timeout=30)  # Added timeout
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)
        data_to_upload = BytesIO(response.content)
        data_length = len(response.content)
        print(f"Successfully downloaded {filename} ({data_length} bytes).")
    except requests.exceptions.RequestException as e:
        print(f"Error downloading file from {config.WRM_FAILURES_DATA_URL}: {e}")
        return

    # Check if bucket exists, create if it doesn't
    try:
        if not minio_client.bucket_exists(config.BUCKET_NAME):
            minio_client.make_bucket(config.BUCKET_NAME)
            print(f"Bucket '{config.BUCKET_NAME}' created.")
        else:
            print(f"Bucket '{config.BUCKET_NAME}' already exists.")
    except S3Error as e:
        print(f"Error checking or creating bucket '{config.BUCKET_NAME}': {e}")
        return

    # Upload to MinIO
    print(f"Uploading '{filename}' to MinIO bucket '{config.BUCKET_NAME}' as '{s3_object_name}'...")
    try:
        minio_client.put_object(
            bucket_name=config.BUCKET_NAME,
            object_name=s3_object_name,
            data=data_to_upload,
            length=data_length,
            content_type=f"text/{config.CSV_EXTENSION}"  # Assuming CSV, adjust if different
        )
        print(f"Successfully uploaded '{filename}' to '{config.BUCKET_NAME}/{s3_object_name}'.")
    except S3Error as e:
        print(f"MinIO S3 Error uploading file '{filename}': {e}")
    except Exception as e:
        print(f"An unexpected error occurred during upload: {e}")

if __name__ == "__main__":
    main()