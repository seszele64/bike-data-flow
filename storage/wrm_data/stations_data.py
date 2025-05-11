#!/usr/bin/env python3

import os
import requests
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import dotenv

# Import the get_minio_client function from create_client.py
from pathlib import Path
from ..create_client import get_minio_client
# Import configuration
from .config import (
    WRM_STATIONS_DATA_URL,
    WRM_STATIONS_S3_PREFIX,
    WRM_STATION_BASE_FILENAME,
    BUCKET_NAME,
    ENV_PATH  # Import ENV_PATH
)

# Optional MinIO imports for S3 upload functionality
try:
    from minio import Minio
    from minio.error import S3Error
    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False
    print("MinIO library not available. File will only be saved locally.")


def main():
    # Get current time and adjust for GMT+1
    current_time = datetime.utcnow() + timedelta(hours=1)
    
    # Create filename with GMT+1 time
    filename = f"{WRM_STATION_BASE_FILENAME}_{current_time.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    
    # URL to download
    url = WRM_STATIONS_DATA_URL
    
    # Download the content
    print(f"Attempting to download data from: {url}")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        data_to_upload = BytesIO(response.content)
        data_length = len(response.content)
        print(f"Successfully downloaded {filename} ({data_length} bytes).")
    except requests.exceptions.RequestException as e:
        print(f"Error downloading file from {url}: {e}")
        return

    # Optional MinIO upload
    if not MINIO_AVAILABLE:
        print("MinIO library not available. Cannot upload to S3.")
        return
        
    # Load .env file using the path from config
    if ENV_PATH and Path(ENV_PATH).exists():
        dotenv.load_dotenv(dotenv_path=ENV_PATH)
        print(f"Loaded .env file from: {ENV_PATH}")
    else:
        print(f"Warning: .env file not found at configured path: {ENV_PATH}. Relying on pre-set environment variables or default .env location.")
        # Attempt to load from default location if not found, or rely on system env vars
        dotenv.load_dotenv()

    # Initialize MinIO client
    # Pass the ENV_PATH from config to get_minio_client
    minio_client = get_minio_client(dotenv_path=ENV_PATH) 
    if not minio_client:
        return
    
    # Get bucket name from environment or use a default from config
    bucket_name = BUCKET_NAME 
    if not bucket_name:
        print("Error: BUCKET_NAME is not configured.")
        return

    # Create date path for S3 object name
    date_path = current_time.strftime('%Y/%m/%d')
    s3_object_name = f"{WRM_STATIONS_S3_PREFIX}{date_path}/{filename}"
    
    # Check if bucket exists, create if it doesn't
    try:
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)
            print(f"Bucket '{bucket_name}' created.")
        else:
            print(f"Bucket '{bucket_name}' already exists.")
    except S3Error as e:
        print(f"Error checking or creating bucket '{bucket_name}': {e}")
        return

    # Upload to MinIO
    print(f"Uploading '{filename}' to MinIO bucket '{bucket_name}' as '{s3_object_name}'...")
    try:
        data_to_upload.seek(0)  # Reset BytesIO position to the beginning
        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=s3_object_name,
            data=data_to_upload,
            length=data_length,
            content_type="text/plain"
        )
        print(f"Successfully uploaded '{filename}' to '{bucket_name}/{s3_object_name}'.")
    except S3Error as e:
        print(f"MinIO S3 Error uploading file '{filename}': {e}")
    except Exception as e:
        print(f"An unexpected error occurred during upload: {e}")

if __name__ == "__main__":
    main()