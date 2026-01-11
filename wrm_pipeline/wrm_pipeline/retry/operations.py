"""S3 operation helpers with built-in retry logic.

This module provides ready-to-use functions for common S3 operations
with automatic retry on transient failures.
"""

import logging
import time
from typing import Optional, Any, Dict, Union, BinaryIO
from contextlib import contextmanager

import boto3
from botocore.exceptions import (
    ClientError,
    BotoCoreError,
    ConnectionError as BotoConnectionError,
    EndpointConnectionError,
    HTTPClientError,
)

from .config import RetryConfiguration
from .decorators import RetryPresets
from .exceptions import RetryExhaustedException


logger = logging.getLogger(__name__)


# Type aliases
S3Client = Any  # boto3 client or resource
BucketKey = tuple[str, str]  # (bucket, key)


class S3OperationError(Exception):
    """Base exception for S3 operation errors."""
    pass


class S3UploadError(S3OperationError):
    """Exception raised when S3 upload fails permanently."""
    pass


class S3DownloadError(S3OperationError):
    """Exception raised when S3 download fails permanently."""
    pass


class S3DeleteError(S3OperationError):
    """Exception raised when S3 delete fails permanently."""
    pass


def _extract_s3_error_code(exception: ClientError) -> str:
    """Extract S3 error code from ClientError.
    
    Args:
        exception: boto3 ClientError
        
    Returns:
        Error code string (e.g., 'NoSuchKey', 'AccessDenied')
    """
    return exception.response.get("Error", {}).get("Code", "Unknown")


def retry_s3_upload(
    s3_client: S3Client,
    bucket: str,
    key: str,
    body: Union[bytes, BinaryIO, str],
    content_type: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
    extra_args: Optional[Dict[str, Any]] = None,
    config: Optional[RetryConfiguration] = None,
) -> Dict[str, Any]:
    """Upload a file or data to S3 with automatic retry.
    
    Handles throttling, rate limits, and network issues with exponential backoff.
    
    Args:
        s3_client: boto3 S3 client or resource
        bucket: S3 bucket name
        key: Object key (path) in the bucket
        body: File content (bytes, file-like object, or string)
        content_type: Optional MIME type (auto-detected if not provided)
        metadata: Optional object metadata
        extra_args: Additional arguments for put_object (ACL, ServerSideEncryption, etc.)
        config: Retry configuration (uses S3_UPLOAD preset if None)
        
    Returns:
        Response from S3 put_object
        
    Raises:
        S3UploadError: If upload fails after all retries
        RetryExhaustedException: If retries are exhausted
        
    Examples:
        # Simple upload
        response = retry_s3_upload(
            s3_client,
            bucket="my-bucket",
            key="data/file.parquet",
            body=parquet_data,
        )
        
        # Upload with metadata
        response = retry_s3_upload(
            s3_client,
            bucket="my-bucket",
            key="data/file.parquet",
            body=data,
            metadata={"source": "pipeline", "version": "1"},
        )
    """
    config = config or RetryPresets.S3_UPLOAD
    
    extra_args = extra_args or {}
    if metadata:
        extra_args["Metadata"] = metadata
    if content_type:
        extra_args["ContentType"] = content_type
    
    call_count = 0
    last_error = None
    
    while True:
        call_count += 1
        try:
            if hasattr(s3_client, 'put_object'):
                # S3 Client
                response = s3_client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    **extra_args,
                )
            else:
                # S3 Object (from resource)
                obj = s3_client.Object(bucket, key)
                response = obj.put(
                    Body=body,
                    **extra_args,
                )
            
            logger.info(
                f"S3 upload successful: s3://{bucket}/{key} "
                f"(attempt {call_count})"
            )
            return response
            
        except ClientError as e:
            error_code = _extract_s3_error_code(e)
            last_error = e
            
            # Check if non-retryable
            if error_code in ("AccessDenied", "NoSuchBucket", "InvalidBucketName"):
                logger.error(
                    f"S3 upload failed (non-retryable): s3://{bucket}/{key} "
                    f"- Error: {error_code}"
                )
                raise S3UploadError(
                    f"S3 upload failed: {error_code}"
                ) from e
            
            # Retryable error - check if retries exhausted
            if call_count >= config.max_attempts:
                logger.error(
                    f"S3 upload failed after {call_count} attempts: "
                    f"s3://{bucket}/{key} - Error: {error_code}"
                )
                raise RetryExhaustedException(
                    message=f"S3 upload failed after {config.max_attempts} attempts",
                    attempts=config.max_attempts,
                    last_exception=e,
                ) from e
            
            # Calculate delay with exponential backoff
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            
            logger.warning(
                f"S3 upload retry {call_count}/{config.max_attempts}: "
                f"s3://{bucket}/{key} - Error: {error_code}, "
                f"retrying in {delay:.1f}s"
            )
            
            time.sleep(delay)
            
        except (BotoCoreError, BotoConnectionError, EndpointConnectionError, HTTPClientError) as e:
            last_error = e
            
            if call_count >= config.max_attempts:
                logger.error(
                    f"S3 upload failed after {call_count} attempts: "
                    f"s3://{bucket}/{key} - Error: {type(e).__name__}"
                )
                raise RetryExhaustedException(
                    message=f"S3 upload failed after {config.max_attempts} attempts",
                    attempts=config.max_attempts,
                    last_exception=e,
                ) from e
            
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            
            logger.warning(
                f"S3 upload retry {call_count}/{config.max_attempts}: "
                f"s3://{bucket}/{key} - {type(e).__name__}, "
                f"retrying in {delay:.1f}s"
            )
            
            time.sleep(delay)


def retry_s3_download(
    s3_client: S3Client,
    bucket: str,
    key: str,
    filepath: Optional[str] = None,
    version_id: Optional[str] = None,
    config: Optional[RetryConfiguration] = None,
) -> Union[bytes, str, Dict[str, Any]]:
    """Download an object from S3 with automatic retry.
    
    Handles network issues, timeouts, and throttling with exponential backoff.
    
    Args:
        s3_client: boto3 S3 client or resource
        bucket: S3 bucket name
        key: Object key (path) in the bucket
        filepath: Optional local filepath to download to
        version_id: Optional object version
        config: Retry configuration (uses S3_DOWNLOAD preset if None)
        
    Returns:
        If filepath provided: filepath string
        Otherwise: bytes content
        
    Raises:
        S3DownloadError: If download fails after all retries
        RetryExhaustedException: If retries are exhausted
        
    Examples:
        # Download to memory
        data = retry_s3_download(
            s3_client,
            bucket="my-bucket",
            key="data/file.parquet",
        )
        
        # Download to file
        filepath = retry_s3_download(
            s3_client,
            bucket="my-bucket",
            key="data/file.parquet",
            filepath="/tmp/downloaded.parquet",
        )
    """
    config = config or RetryPresets.S3_DOWNLOAD
    
    call_count = 0
    last_error = None
    
    while True:
        call_count += 1
        try:
            if hasattr(s3_client, 'download_file'):
                # S3 Client with download_file
                if filepath:
                    s3_client.download_file(bucket, key, filepath)
                    logger.info(
                        f"S3 download successful: s3://{bucket}/{key} -> {filepath} "
                        f"(attempt {call_count})"
                    )
                    return filepath
                else:
                    # Download to bytes
                    response = s3_client.get_object(Bucket=bucket, Key=key)
                    content = response['Body'].read()
                    logger.info(
                        f"S3 download successful: s3://{bucket}/{key} "
                        f"(attempt {call_count}, {len(content)} bytes)"
                    )
                    return content
            else:
                # S3 Object (from resource)
                obj = s3_client.Object(bucket, key)
                if filepath:
                    obj.download_file(filepath)
                    logger.info(
                        f"S3 download successful: s3://{bucket}/{key} -> {filepath} "
                        f"(attempt {call_count})"
                    )
                    return filepath
                else:
                    response = obj.get()
                    content = response['Body'].read()
                    logger.info(
                        f"S3 download successful: s3://{bucket}/{key} "
                        f"(attempt {call_count}, {len(content)} bytes)"
                    )
                    return content
            
        except ClientError as e:
            error_code = _extract_s3_error_code(e)
            last_error = e
            
            # Check if non-retryable
            if error_code in ("NoSuchKey", "AccessDenied", "InvalidObjectState"):
                logger.error(
                    f"S3 download failed (non-retryable): s3://{bucket}/{key} "
                    f"- Error: {error_code}"
                )
                raise S3DownloadError(
                    f"S3 download failed: {error_code}"
                ) from e
            
            if call_count >= config.max_attempts:
                logger.error(
                    f"S3 download failed after {call_count} attempts: "
                    f"s3://{bucket}/{key} - Error: {error_code}"
                )
                raise RetryExhaustedException(
                    message=f"S3 download failed after {config.max_attempts} attempts",
                    attempts=config.max_attempts,
                    last_exception=e,
                ) from e
            
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            
            logger.warning(
                f"S3 download retry {call_count}/{config.max_attempts}: "
                f"s3://{bucket}/{key} - Error: {error_code}, "
                f"retrying in {delay:.1f}s"
            )
            
            time.sleep(delay)
            
        except (BotoCoreError, BotoConnectionError, EndpointConnectionError, HTTPClientError) as e:
            last_error = e
            
            if call_count >= config.max_attempts:
                logger.error(
                    f"S3 download failed after {call_count} attempts: "
                    f"s3://{bucket}/{key} - Error: {type(e).__name__}"
                )
                raise RetryExhaustedException(
                    message=f"S3 download failed after {config.max_attempts} attempts",
                    attempts=config.max_attempts,
                    last_exception=e,
                ) from e
            
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            
            logger.warning(
                f"S3 download retry {call_count}/{config.max_attempts}: "
                f"s3://{bucket}/{key} - {type(e).__name__}, "
                f"retrying in {delay:.1f}s"
            )
            
            time.sleep(delay)


def retry_s3_delete(
    s3_client: S3Client,
    bucket: str,
    key: str,
    version_id: Optional[str] = None,
    config: Optional[RetryConfiguration] = None,
) -> bool:
    """Delete an object from S3 with automatic retry.
    
    Args:
        s3_client: boto3 S3 client or resource
        bucket: S3 bucket name
        key: Object key to delete
        version_id: Optional version ID for versioned objects
        config: Retry configuration (uses default if None)
        
    Returns:
        True if deleted successfully
        
    Raises:
        S3DeleteError: If delete fails after all retries
        
    Examples:
        # Delete single object
        retry_s3_delete(s3_client, bucket="my-bucket", key="old/file.parquet")
        
        # Delete specific version
        retry_s3_delete(
            s3_client,
            bucket="my-bucket",
            key="file.parquet",
            version_id="version-id-string",
        )
    """
    config = config or RetryConfiguration(
        max_attempts=3,
        base_delay=0.5,
        max_delay=10.0,
        jitter=0.5,
    )
    
    call_count = 0
    
    while True:
        call_count += 1
        try:
            if hasattr(s3_client, 'delete_object'):
                kwargs = {"Bucket": bucket, "Key": key}
                if version_id:
                    kwargs["VersionId"] = version_id
                s3_client.delete_object(**kwargs)
            else:
                obj = s3_client.Object(bucket, key)
                if version_id:
                    obj.delete(VersionId=version_id)
                else:
                    obj.delete()
            
            logger.info(
                f"S3 delete successful: s3://{bucket}/{key} "
                f"(attempt {call_count})"
            )
            return True
            
        except ClientError as e:
            error_code = _extract_s3_error_code(e)
            
            # NoSuchKey is not an error - object already deleted
            if error_code == "NoSuchKey":
                logger.info(
                    f"S3 delete: object already removed: s3://{bucket}/{key}"
                )
                return True
            
            if error_code in ("AccessDenied", "InvalidBucketState"):
                logger.error(
                    f"S3 delete failed (non-retryable): s3://{bucket}/{key} "
                    f"- Error: {error_code}"
                )
                raise S3DeleteError(f"S3 delete failed: {error_code}") from e
            
            if call_count >= config.max_attempts:
                logger.error(
                    f"S3 delete failed after {call_count} attempts: "
                    f"s3://{bucket}/{key} - Error: {error_code}"
                )
                raise RetryExhaustedException(
                    message=f"S3 delete failed after {config.max_attempts} attempts",
                    attempts=config.max_attempts,
                    last_exception=e,
                ) from e
            
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            
            logger.warning(
                f"S3 delete retry {call_count}/{config.max_attempts}: "
                f"s3://{bucket}/{key} - Error: {error_code}, "
                f"retrying in {delay:.1f}s"
            )
            time.sleep(delay)
            
        except (BotoCoreError, BotoConnectionError) as e:
            if call_count >= config.max_attempts:
                raise RetryExhaustedException(
                    message=f"S3 delete failed after {config.max_attempts} attempts",
                    attempts=config.max_attempts,
                    last_exception=e,
                ) from e
            
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            time.sleep(delay)


def retry_s3_list_objects(
    s3_client: S3Client,
    bucket: str,
    prefix: Optional[str] = None,
    max_keys: int = 1000,
    config: Optional[RetryConfiguration] = None,
) -> list:
    """List objects in S3 with automatic retry.
    
    Args:
        s3_client: boto3 S3 client or resource
        bucket: S3 bucket name
        prefix: Optional prefix to filter objects
        max_keys: Maximum keys per request (default 1000)
        config: Retry configuration
        
    Returns:
        List of object keys (strings)
        
    Raises:
        S3OperationError: If list operation fails
    """
    config = config or RetryConfiguration(
        max_attempts=3,
        base_delay=0.5,
        max_delay=10.0,
        jitter=0.5,
    )
    
    all_keys = []
    continuation_token = None
    call_count = 0
    
    while True:
        call_count += 1
        try:
            kwargs = {"Bucket": bucket, "MaxKeys": max_keys}
            if prefix:
                kwargs["Prefix"] = prefix
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            
            response = s3_client.list_objects_v2(**kwargs)
            keys = [obj["Key"] for obj in response.get("Contents", [])]
            all_keys.extend(keys)
            
            # Check for more results
            if response.get("IsTruncated"):
                continuation_token = response.get("NextContinuationToken")
            else:
                break
            
        except ClientError as e:
            error_code = _extract_s3_error_code(e)
            
            if call_count >= config.max_attempts:
                raise S3OperationError(
                    f"S3 list_objects failed after {config.max_attempts} attempts: "
                    f"{error_code}"
                ) from e
            
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            time.sleep(delay)
            
        except (BotoCoreError, BotoConnectionError) as e:
            if call_count >= config.max_attempts:
                raise S3OperationError(
                    f"S3 list_objects failed after {config.max_attempts} attempts"
                ) from e
            
            delay = min(
                config.base_delay * (2 ** (call_count - 1)) + config.jitter,
                config.max_delay,
            )
            time.sleep(delay)
    
    return all_keys


@contextmanager
def s3_operation_context(
    s3_client: S3Client,
    bucket: str,
    config: Optional[RetryConfiguration] = None,
):
    """Context manager for atomic S3 operations with retry.
    
    Provides a unified interface for multiple S3 operations within
    a single retry context.
    
    Args:
        s3_client: boto3 S3 client
        bucket: Default bucket for operations
        config: Retry configuration
        
    Yields:
        S3OperationHelper instance
        
    Examples:
        with s3_operation_context(s3_client, "my-bucket") as s3:
            s3.upload("key1", data1)
            s3.download("key2", filepath)
    """
    helper = S3OperationHelper(s3_client, bucket, config)
    yield helper


class S3OperationHelper:
    """Helper class for S3 operations with retry.
    
    Provides retry-enabled methods for common S3 operations.
    """
    
    def __init__(
        self,
        s3_client: S3Client,
        bucket: str,
        config: Optional[RetryConfiguration] = None,
    ):
        self.s3_client = s3_client
        self.bucket = bucket
        self.config = config
    
    def upload(
        self,
        key: str,
        body: Union[bytes, BinaryIO, str],
        **kwargs,
    ) -> Dict[str, Any]:
        """Upload with retry."""
        return retry_s3_upload(
            self.s3_client,
            self.bucket,
            key,
            body,
            config=self.config,
            **kwargs,
        )
    
    def download(
        self,
        key: str,
        filepath: Optional[str] = None,
    ) -> Union[bytes, str]:
        """Download with retry."""
        return retry_s3_download(
            self.s3_client,
            self.bucket,
            key,
            filepath=filepath,
            config=self.config,
        )
    
    def delete(
        self,
        key: str,
        **kwargs,
    ) -> bool:
        """Delete with retry."""
        return retry_s3_delete(
            self.s3_client,
            self.bucket,
            key,
            config=self.config,
            **kwargs,
        )
    
    def list(
        self,
        prefix: Optional[str] = None,
        **kwargs,
    ) -> list:
        """List objects with retry."""
        return retry_s3_list_objects(
            self.s3_client,
            self.bucket,
            prefix=prefix,
            config=self.config,
            **kwargs,
        )
