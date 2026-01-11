"""Retry decorator utilities."""

from functools import wraps
from typing import Callable, Type, Optional, Tuple, Any

import tenacity

from botocore.exceptions import ClientError, HTTPClientError

from .config import RetryConfiguration
from .tenacity_base import (
    get_tenacity_decorator,
    get_wait_strategy,
    get_stop_strategy,
    get_retry_strategy,
)
from .exceptions import RetryExhaustedException


# S3 error codes that should NOT be retried (fail immediately)
S3_NON_RETRYABLE_ERROR_CODES = frozenset({
    # Access and permissions errors
    "AccessDenied",
    "AccessDeniedException",
    "InvalidAccessKeyId",
    "InvalidArgument",
    "InvalidBucketName",
    "MalformedPOSTRequest",
    "MissingContentLength",
    "NoSuchBucket",
    "NoSuchKey",
    "NoSuchUpload",
    "ObjectAlreadyExists",
    "ObjectNotAppendable",
    "PermanentRedirect",
    "PreconditionFailed",
    "Redirect",
    "RequestTimeTooSkewed",
    "SignatureDoesNotMatch",
    "TemporaryRedirect",
    "InvalidObjectState",  # Glacier restore required
})

# S3 error codes that ARE retryable (throttling, server errors, network issues)
S3_RETRYABLE_ERROR_CODES = frozenset({
    "Throttling",
    "ThrottlingException",
    "ProvisionedThroughputExceededException",
    "RequestTimeout",
    "RequestTimeoutException",
    "InternalError",
    "ServiceUnavailable",
    "ServiceUnavailableException",
    "SlowDown",
    "503",
    "500",
    "502",
    "504",
})

# S3-specific exception types that are always retryable
S3_RETRYABLE_EXCEPTIONS = (
    HTTPClientError,  # Connection/network errors
    ClientError,  # Will be filtered by error code
)


def is_s3_retryable_error(error: ClientError) -> bool:
    """Check if an S3 error is retryable based on error code.
    
    Args:
        error: boto3 ClientError instance
        
    Returns:
        True if the error is retryable, False otherwise
    """
    error_code = error.response.get("Error", {}).get("Code", "")
    
    # Check if error code is in non-retryable list
    if error_code in S3_NON_RETRYABLE_ERROR_CODES:
        return False
    
    # Check if error code is in retryable list
    if error_code in S3_RETRYABLE_ERROR_CODES:
        return True
    
    # Retry on any 5xx HTTP errors
    if error_code.startswith("5"):
        return True
    
    return False


def _s3_retry_if_exception(exception: Exception) -> bool:
    """Predicate to determine if an exception should be retried for S3 operations.
    
    - Retryable: HTTPClientError (network/connection), ClientError with retryable codes
    - Non-retryable: ClientError with non-retryable codes (AccessDenied, NoSuchKey, etc.)
    """
    if isinstance(exception, ClientError):
        return is_s3_retryable_error(exception)
    
    # Retry on HTTPClientError (covers connection errors, timeouts, etc.)
    if isinstance(exception, HTTPClientError):
        return True
    
    return False


def with_retry(
    config: Optional[RetryConfiguration] = None,
    exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    max_attempts: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    exponential_base: Optional[float] = None,
    jitter: Optional[float] = None,
    respect_retry_after: bool = True,
) -> Callable:
    """Decorator to add retry logic with exponential backoff to a function.
    
    Can be used with explicit configuration or individual parameters.
    
    Args:
        config: Optional RetryConfiguration instance
        exceptions: Tuple of exception types to retry on
        max_attempts: Maximum retry attempts (default: 3)
        base_delay: Base delay in seconds (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 60.0)
        exponential_base: Base for exponential backoff (default: 2.0)
        jitter: Random jitter in seconds (default: 0.1)
        respect_retry_after: Whether to respect Retry-After headers
        
    Returns:
        Decorated function with retry logic
        
    Examples:
        @with_retry(max_attempts=5, base_delay=0.5)
        def unreliable_function():
            ...
            
        @with_retry(RetryConfiguration(max_attempts=10))
        def another_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        # Build configuration from parameters
        if config is not None:
            effective_config = config
        else:
            effective_config = RetryConfiguration(
                max_attempts=max_attempts or 3,
                base_delay=base_delay or 1.0,
                max_delay=max_delay or 60.0,
                exponential_base=exponential_base or 2.0,
                jitter=jitter or 0.1,
                retry_on_exceptions=exceptions or (Exception,),
                respect_retry_after=respect_retry_after,
            )
        
        tenacity_decorator = get_tenacity_decorator(effective_config)
        
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return tenacity_decorator(func)(*args, **kwargs)
            except tenacity.RetryError as e:
                # Get the last exception from the RetryError's outcome
                last_exception = e.last_attempt.exception() if e.last_attempt else Exception("Unknown retry error")
                raise RetryExhaustedException(
                    message=f"All {effective_config.max_attempts} retry attempts exhausted",
                    attempts=effective_config.max_attempts,
                    last_exception=last_exception,
                ) from last_exception
        
        return wrapper
    return decorator


def with_s3_retry(
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 1.0,
) -> Callable:
    """Decorator to add S3-specific retry logic to a function.
    
    Handles AWS S3 throttling, rate limits, and network issues.
    Automatically filters non-retryable errors like AccessDenied, NoSuchKey, etc.
    
    Args:
        max_attempts: Maximum retry attempts (default: 5)
        base_delay: Base delay in seconds (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 30.0)
        jitter: Random jitter in seconds (default: 1.0)
        
    Returns:
        Decorated function with S3-specific retry logic
        
    Examples:
        @with_s3_retry(max_attempts=5)
        def upload_to_s3(bucket, key, data):
            s3_client.put_object(Bucket=bucket, Key=key, Body=data)
    """
    def decorator(func: Callable) -> Callable:
        config = RetryConfiguration(
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter=jitter,
            retry_on_exceptions=S3_RETRYABLE_EXCEPTIONS,
        )
        
        tenacity_decorator = get_tenacity_decorator(config)
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return tenacity_decorator(func)(*args, **kwargs)
            except tenacity.RetryError as e:
                last_exception = e.last_attempt.exception() if e.last_attempt else Exception("Unknown retry error")
                raise RetryExhaustedException(
                    message=f"All {max_attempts} S3 retry attempts exhausted",
                    attempts=max_attempts,
                    last_exception=last_exception,
                ) from last_exception
        
        return wrapper
    return decorator


class RetryPresets:
    """Pre-configured retry settings for common scenarios."""
    
    # S3 Operations
    S3_UPLOAD = RetryConfiguration(
        max_attempts=5,
        base_delay=1.0,
        max_delay=30.0,
        jitter=1.0,
        retry_on_exceptions=S3_RETRYABLE_EXCEPTIONS,
    )
    
    S3_DOWNLOAD = RetryConfiguration(
        max_attempts=5,
        base_delay=0.5,
        max_delay=30.0,
        jitter=0.5,
        retry_on_exceptions=S3_RETRYABLE_EXCEPTIONS,
    )
    
    # Heavy operations (e.g., large uploads)
    S3_HEAVY_OPERATION = RetryConfiguration(
        max_attempts=3,
        base_delay=2.0,
        max_delay=60.0,
        jitter=2.0,
        retry_on_exceptions=S3_RETRYABLE_EXCEPTIONS,
    )
    
    # API Calls (generic HTTP/API operations)
    API_CALL = RetryConfiguration(
        max_attempts=3,
        base_delay=0.5,
        max_delay=10.0,
        jitter=0.5,
        retry_on_exceptions=(HTTPClientError, Exception),
    )


def _apply_tenacity_decorator(func: Callable, config: RetryConfiguration) -> Callable:
    """Internal helper to apply tenacity decorator with configuration."""
    tenacity_decorator = get_tenacity_decorator(config)
    
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return tenacity_decorator(func)(*args, **kwargs)
        except tenacity.RetryError as e:
            last_exception = e.last_attempt.exception() if e.last_attempt else Exception("Unknown")
            raise RetryExhaustedException(
                message=f"All {config.max_attempts} retry attempts exhausted",
                attempts=config.max_attempts,
                last_exception=last_exception,
            ) from last_exception
    
    return wrapper


def with_s3_upload_retry(func: Callable) -> Callable:
    """Decorator for S3 upload operations with preset configuration.
    
    Uses S3_UPLOAD preset with 5 attempts, 1s base delay, 30s max delay.
    """
    return _apply_tenacity_decorator(func, RetryPresets.S3_UPLOAD)


def with_s3_download_retry(func: Callable) -> Callable:
    """Decorator for S3 download operations with preset configuration.
    
    Uses S3_DOWNLOAD preset with 5 attempts, 0.5s base delay, 30s max delay.
    """
    return _apply_tenacity_decorator(func, RetryPresets.S3_DOWNLOAD)
