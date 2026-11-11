from dagster import asset, AssetExecutionContext
import requests
import pandas as pd
import ftfy
from io import StringIO, BytesIO
from datetime import datetime, timezone
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List
import pandera as pa
from pandera import Column, DataFrameSchema, Check
import hashlib
import os

from ...config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from ...models.stations import processed_data_schema


class WRMAPIConfig:
    """Configuration for WRM API - supports Vault integration."""
    
    def __init__(self):
        self._api_url = None
    
    @property
    def api_url(self) -> str:
        """Get WRM API URL from Vault or environment."""
        if self._api_url is None:
            # Try to get from Vault if enabled
            vault_addr = os.environ.get("VAULT_ADDR")
            if vault_addr and os.environ.get("VAULT_ENABLED", "false").lower() == "true":
                try:
                    from ...vault.client import VaultClient
                    from ...vault.models import VaultConnectionConfig
                    
                    config = VaultConnectionConfig(
                        vault_addr=vault_addr,
                        auth_method="approle",
                        role_id=os.environ.get("VAULT_ROLE_ID"),
                        secret_id=os.environ.get("VAULT_SECRET_ID"),
                    )
                    client = VaultClient(config)
                    secret = client.get_secret("bike-data-flow/production/api")
                    client.close()
                    self._api_url = secret.data.get("wrm_api_url", "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw")
                except Exception:
                    self._api_url = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"
            else:
                self._api_url = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"
        return self._api_url


# Global config instance
wrm_api_config = WRMAPIConfig()


def _list_all_objects_v2(s3_client, bucket: str, prefix: str) -> List[dict]:
    """Return every S3 object under ``prefix``, following continuation tokens.

    ``list_objects_v2`` returns at most 1000 keys per response, so callers that
    select the newest object must page through the full listing or they will
    silently miss objects past the first page. Full object metadata (including
    ``LastModified``) is preserved. Pagination stops when the response is not
    truncated, or defensively when a truncated response omits a continuation
    token (prevents an infinite loop).
    """
    objects: List[dict] = []
    continuation_token = None

    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token

        response = s3_client.list_objects_v2(**kwargs)
        objects.extend(response.get("Contents", []))

        if not response.get("IsTruncated"):
            break

        continuation_token = response.get("NextContinuationToken")
        if not continuation_token:
            break

    return objects


@asset(
    name="wrm_stations_raw_data",
    # No partitions_def here - it only fetches current data
    compute_kind="requests",
    group_name="wrm_data_acquisition",
    required_resource_keys={"s3_resource"},
    # Optional: Add vault resource if you need to retrieve secrets during execution
    # required_resource_keys={"s3_resource", "vault"},
)
def wrm_stations_raw_data_asset(context: AssetExecutionContext) -> str:
    """Download raw station data from WRM API and store in S3 without validation"""
    
    # Get S3 client directly from the resource
    s3_client = context.resources.s3_resource
    
    # API endpoint for WRM bike stations
    # Supports Vault integration: if VAULT_ENABLED=true, reads from Vault
    api_url = wrm_api_config.api_url
    
    try:
        # Download data from API
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        
        # Fix encoding issues
        fixed_data = ftfy.fix_text(response.text)
        
        # Calculate hash of the new data
        new_data_hash = hashlib.sha256(fixed_data.encode('utf-8')).hexdigest()
        context.log.info(f"New data hash: {new_data_hash}")
        
        # Capture current time for file naming (UTC for deterministic partitions)
        current_time = datetime.now(timezone.utc)
        timestamp = current_time.strftime("%Y-%m-%d_%H-%M-%S")
        date_partition = current_time.strftime("%Y-%m-%d")
        
        # Check for duplicate data by comparing with the most recent file across all dates
        raw_s3_prefix = f"{WRM_STATIONS_S3_PREFIX}raw/"
        
        try:
            # List all files in the raw directory across every S3 page
            all_objects = _list_all_objects_v2(s3_client, BUCKET_NAME, raw_s3_prefix)
            
            if all_objects:
                # Filter to only include .txt files (exclude directories)
                txt_files = [obj for obj in all_objects if obj['Key'].endswith('.txt')]
                
                if txt_files:
                    # Get the most recent file across all date partitions
                    most_recent_file = max(txt_files, key=lambda x: x['LastModified'])
                    most_recent_key = most_recent_file['Key']
                    
                    context.log.info(f"Found most recent file across all dates: {most_recent_key}")
                    
                    # Download and hash the most recent file
                    try:
                        recent_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=most_recent_key)
                        recent_data = recent_response['Body'].read().decode('utf-8')
                        recent_data_hash = hashlib.sha256(recent_data.encode('utf-8')).hexdigest()
                        
                        context.log.info(f"Most recent file hash: {recent_data_hash}")
                        
                        # Compare hashes
                        if new_data_hash == recent_data_hash:
                            context.log.info("Data is identical to the most recent file. Skipping upload to avoid duplication.")
                            
                            # Add output metadata for duplicate detection
                            context.add_output_metadata({
                                "fetch_timestamp": current_time.isoformat(),
                                "data_date": date_partition,
                                "duplicate_detected": True,
                                "existing_file": most_recent_key,
                                "data_hash": new_data_hash,
                                "data_size_bytes": len(fixed_data.encode('utf-8')),
                                "skip_reason": "identical_to_recent_file"
                            })
                            
                            # Return the existing file key instead of uploading a new one
                            return most_recent_key
                        else:
                            context.log.info("Data differs from the most recent file. Proceeding with upload.")
                            
                    except Exception as e:
                        context.log.warning(f"Could not download or hash recent file {most_recent_key}: {e}")
                        context.log.info("Proceeding with upload due to comparison failure.")
                else:
                    context.log.info("No .txt files found in raw directory. Proceeding with upload.")
            else:
                context.log.info("No existing files found in raw directory. Proceeding with upload.")
                
        except Exception as e:
            context.log.warning(f"Could not check for existing files: {e}")
            context.log.info("Proceeding with upload due to duplicate check failure.")
        
        # Define S3 key for raw data using current date
        s3_key = f"{WRM_STATIONS_S3_PREFIX}raw/dt={date_partition}/wrm_stations_{timestamp}.txt"
        
        # Upload raw data to S3
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=fixed_data.encode('utf-8'),
            ContentType='text/plain'
        )
        
        context.log.info(f"Raw station data uploaded to S3: {s3_key}")
        
        # Add output metadata
        context.add_output_metadata({
            "fetch_timestamp": current_time.isoformat(),
            "data_date": date_partition,  # Track what date this data represents
            "s3_key": s3_key,
            "data_size_bytes": len(fixed_data.encode('utf-8')),
            "duplicate_detected": False,
            "data_hash": new_data_hash
        })
        
        return s3_key
        
    except Exception as e:
        context.log.error(f"Failed to download or upload raw station data: {e}")
        raise