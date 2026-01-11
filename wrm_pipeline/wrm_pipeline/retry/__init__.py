"""Retry module for resilient S3 and API operations with exponential backoff."""

from .config import RetryConfiguration
from .exceptions import RetryExhaustedException, CircuitOpenException
from .circuit_breaker import CircuitState, CircuitBreakerConfiguration
from .tenacity_base import get_tenacity_decorator
from .decorators import (
    with_retry,
    with_s3_retry,
    with_s3_upload_retry,
    with_s3_download_retry,
    with_api_retry,
    with_api_call_retry,
    get_api_wait_strategy,
    RetryPresets,
    S3_NON_RETRYABLE_ERROR_CODES,
    S3_RETRYABLE_ERROR_CODES,
    S3_RETRYABLE_EXCEPTIONS,
    is_s3_retryable_error,
    API_RETRYABLE_EXCEPTIONS,
    API_NON_RETRYABLE_STATUS_CODES,
    is_api_retryable_error,
    is_api_retryable_error_state,
    RetryAfterWaitStrategy,
)

__all__ = [
    "RetryConfiguration",
    "RetryExhaustedException",
    "CircuitOpenException",
    "CircuitState",
    "CircuitBreakerConfiguration",
    "get_tenacity_decorator",
    "with_retry",
    "with_s3_retry",
    "with_s3_upload_retry",
    "with_s3_download_retry",
    "with_api_retry",
    "with_api_call_retry",
    "get_api_wait_strategy",
    "RetryPresets",
    "S3_NON_RETRYABLE_ERROR_CODES",
    "S3_RETRYABLE_ERROR_CODES",
    "S3_RETRYABLE_EXCEPTIONS",
    "is_s3_retryable_error",
    "API_RETRYABLE_EXCEPTIONS",
    "API_NON_RETRYABLE_STATUS_CODES",
    "is_api_retryable_error",
    "is_api_retryable_error_state",
    "RetryAfterWaitStrategy",
]

__version__ = "0.2.0"
