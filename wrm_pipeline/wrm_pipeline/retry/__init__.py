"""Retry module for resilient S3 and API operations with exponential backoff."""

from .config import RetryConfiguration
from .exceptions import RetryExhaustedException, CircuitOpenException
from .circuit_breaker import CircuitState, CircuitBreakerConfiguration
from .decorators import (
    with_retry,
    with_s3_retry,
    with_s3_upload_retry,
    with_s3_download_retry,
    RetryPresets,
    S3_NON_RETRYABLE_ERROR_CODES,
    S3_RETRYABLE_ERROR_CODES,
    is_s3_retryable_error,
    S3_RETRYABLE_EXCEPTIONS,
)

__all__ = [
    "RetryConfiguration",
    "RetryExhaustedException",
    "CircuitOpenException",
    "CircuitState",
    "CircuitBreakerConfiguration",
    "with_retry",
    "with_s3_retry",
    "with_s3_upload_retry",
    "with_s3_download_retry",
    "RetryPresets",
    "S3_NON_RETRYABLE_ERROR_CODES",
    "S3_RETRYABLE_ERROR_CODES",
    "is_s3_retryable_error",
    "S3_RETRYABLE_EXCEPTIONS",
]

__version__ = "0.2.0"
