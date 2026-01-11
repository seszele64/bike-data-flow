"""Retry decorator utilities."""

from functools import wraps
from typing import Callable, Type, Optional, Tuple, Any
import copy
import logging
import email.utils

import tenacity

from botocore.exceptions import ClientError, HTTPClientError
from requests.exceptions import (
    ConnectionError,
    Timeout,
    TooManyRedirects,
    HTTPError,
    RequestException,
)

from .config import RetryConfiguration
from .tenacity_base import (
    get_tenacity_decorator,
    get_wait_strategy,
    get_stop_strategy,
    get_retry_strategy,
)
from .exceptions import RetryExhaustedException

logger = logging.getLogger(__name__)


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


# =============================================================================
# HTTP/API Retry Exceptions and Constants
# =============================================================================

# HTTP-specific retryable exceptions from requests library
API_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    Timeout,
    TooManyRedirects,
    HTTPError,
    RequestException,  # Will filter by status code
)

# Non-retryable HTTP status codes (4xx except 429)
API_NON_RETRYABLE_STATUS_CODES = frozenset({
    400,  # Bad Request
    401,  # Unauthorized
    403,  # Forbidden
    404,  # Not Found
    405,  # Method Not Allowed
    406,  # Not Acceptable
    407,  # Proxy Authentication Required
    408,  # Request Timeout
    409,  # Conflict
    410,  # Gone
    411,  # Length Required
    412,  # Precondition Failed
    413,  # Payload Too Large
    414,  # URI Too Long
    415,  # Unsupported Media Type
    416,  # Range Not Satisfiable
    417,  # Expectation Failed
    418,  # I'm a teapot
    421,  # Misdirected Request
    422,  # Unprocessable Entity
    423,  # Locked
    424,  # Failed Dependency
    426,  # Upgrade Required
    428,  # Precondition Required
    431,  # Request Header Fields Too Large
    451,  # Unavailable For Legal Reasons
})


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


# =============================================================================
# HTTP/API Retry Functions and Classes
# =============================================================================

def is_api_retryable_error_state(retry_state: tenacity.RetryCallState) -> bool:
    """Check if an HTTP error is retryable based on status code (tenacity 8+ version).
    
    Args:
        retry_state: Tenacity RetryCallState containing the exception
        
    Returns:
        True if the error is retryable, False otherwise
    """
    if retry_state.outcome is None:
        return False
    
    exception = retry_state.outcome.exception()
    if exception is None:
        return False  # No exception means success, don't retry
    
    if isinstance(exception, HTTPError):
        status_code = exception.response.status_code if exception.response else None
        if status_code:
            # Retry on 429 (rate limit) and 5xx (server errors)
            if status_code == 429:
                return True
            if 500 <= status_code < 600:
                return True
            # Don't retry on other 4xx errors
            if status_code in API_NON_RETRYABLE_STATUS_CODES:
                return False
    # Default to retry for connection/timeout errors (not HTTPError)
    return True


def is_api_retryable_error(exception: Exception) -> bool:
    """Check if an HTTP error is retryable based on status code.
    
    Args:
        exception: Exception instance (HTTPError for HTTP errors)
        
    Returns:
        True if the error is retryable, False otherwise
    """
    if isinstance(exception, HTTPError):
        status_code = exception.response.status_code if exception.response else None
        if status_code:
            # Retry on 429 (rate limit) and 5xx (server errors)
            if status_code == 429:
                return True
            if 500 <= status_code < 600:
                return True
            # Don't retry on other 4xx errors
            if status_code in API_NON_RETRYABLE_STATUS_CODES:
                return False
    # Default to retry for connection/timeout errors (not HTTPError)
    return True


class RetryAfterWaitStrategy:
    """Wait strategy that respects Retry-After headers from HTTP responses.
    
    Uses Retry-After header value if available, otherwise falls back to
    exponential backoff with jitter.
    """
    
    def __init__(
        self,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        jitter: float = 0.5,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
    
    def __call__(self, retry_state: tenacity.RetryCallState) -> float:
        """Calculate wait time before next retry.
        
        Args:
            retry_state: Current retry state from tenacity
            
        Returns:
            Wait time in seconds
        """
        # Try to extract Retry-After from response
        retry_after = self._get_retry_after_from_response(retry_state)
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        
        # Fall back to exponential backoff
        attempt = retry_state.next_action.next_attempt
        exponential_delay = self.base_delay * (2 ** (attempt - 1))
        return min(exponential_delay + self.jitter, self.max_delay)
    
    def _get_retry_after_from_response(
        self, 
        retry_state: tenacity.RetryCallState
    ) -> Optional[float]:
        """Extract Retry-After value from HTTP response.
        
        Args:
            retry_state: Current retry state
            
        Returns:
            Retry-After delay in seconds, or None if not available
        """
        if not hasattr(retry_state, 'outcome') or retry_state.outcome is None:
            return None
        
        outcome = retry_state.outcome
        if not hasattr(outcome, 'exception'):
            return None
        
        exception = outcome.exception()
        if not isinstance(exception, HTTPError):
            return None
        
        response = getattr(exception, 'response', None)
        if response is None:
            return None
        
        # Check Retry-After header (case-insensitive)
        retry_after = response.headers.get('Retry-After') or response.headers.get('retry-after')
        # Ensure retry_after is a string (not a Mock or other object)
        if retry_after and isinstance(retry_after, str):
            return self._parse_retry_after(retry_after)
        
        return None
    
    def _parse_retry_after(self, value: str) -> float:
        """Parse Retry-After header value.
        
        Supports:
        - Seconds: "120"
        - HTTP-date format: "Wed, 21 Oct 2015 07:28:00 GMT"
        
        Args:
            value: Retry-After header value
            
        Returns:
            Delay in seconds
        """
        from datetime import timezone
        
        # Try parsing as seconds
        try:
            return float(value)
        except ValueError:
            pass
        
        # Try parsing as HTTP-date
        try:
            parsed_date = email.utils.parsedate_to_datetime(value)
            # Convert to local time for comparison
            now = email.utils.localtime()
            # Make parsed_date timezone-aware in local time if it's naive
            if parsed_date.tzinfo is None:
                # Assume UTC if no timezone specified
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            # Convert both to UTC for consistent comparison
            now_utc = now.astimezone(timezone.utc) if now.tzinfo else now
            parsed_utc = parsed_date.astimezone(timezone.utc)
            delta = (parsed_utc - now_utc).total_seconds()
            if delta > 0:
                return delta
        except (ValueError, TypeError):
            pass
        
        # Default to 1 second if parsing fails
        logger.warning(f"Failed to parse Retry-After header: {value}")
        return 1.0


def get_api_wait_strategy(
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    jitter: float = 0.5,
) -> RetryAfterWaitStrategy:
    """Create wait strategy that respects Retry-After headers.
    
    Args:
        base_delay: Base delay in seconds
        max_delay: Maximum delay cap
        jitter: Random jitter
        
    Returns:
        RetryAfterWaitStrategy instance
    """
    return RetryAfterWaitStrategy(
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=jitter,
    )


def with_api_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    jitter: float = 0.5,
    respect_retry_after: bool = True,
) -> Callable:
    """Decorator to add API-specific retry logic to a function.
    
    Handles HTTP 5xx errors, timeouts, connection issues, and respects Retry-After headers.
    
    Args:
        max_attempts: Maximum retry attempts (default: 3)
        base_delay: Base delay in seconds (default: 0.5)
        max_delay: Maximum delay cap in seconds (default: 10.0)
        jitter: Random jitter in seconds (default: 0.5)
        respect_retry_after: Whether to respect Retry-After headers (default: True)
        
    Returns:
        Decorated function with API-specific retry logic
        
    Examples:
        @with_api_retry(max_attempts=5, respect_retry_after=True)
        def fetch_api_data(url):
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
    """
    def decorator(func: Callable) -> Callable:
        config = RetryConfiguration(
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter=jitter,
            retry_on_exceptions=API_RETRYABLE_EXCEPTIONS,
            respect_retry_after=respect_retry_after,
        )
        
        # Use custom retry predicate to filter non-retryable HTTP status codes
        # tenacity 8+ passes RetryCallState to retry_if predicates
        decorated_func = get_tenacity_decorator(config, retry_if=is_api_retryable_error_state)(func)
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return decorated_func(*args, **kwargs)
            except tenacity.RetryError as e:
                # Retry exhaustion - all retry attempts exhausted
                last_exception = e.last_attempt.exception() if e.last_attempt else Exception("Unknown retry error")
                raise RetryExhaustedException(
                    message=f"All {max_attempts} API retry attempts exhausted",
                    attempts=max_attempts,
                    last_exception=last_exception,
                ) from last_exception
            except Exception as e:
                # Non-retryable exception - wrap it for consistency
                raise RetryExhaustedException(
                    message=f"API call failed (non-retryable)",
                    attempts=1,
                    last_exception=e,
                ) from e
        
        return wrapper
    return decorator


def with_api_call_retry(
    respect_retry_after: bool = True,
) -> Callable:
    """Decorator for general API calls using API_CALL preset.
    
    Args:
        respect_retry_after: Whether to respect Retry-After headers
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        # Use copy.copy() for RetryConfiguration (dataclass doesn't have model_copy())
        config = copy.copy(RetryPresets.API_CALL)
        config.respect_retry_after = respect_retry_after
        return _apply_tenacity_decorator_with_retry_if(func, config, is_api_retryable_error_state)
    return decorator


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
        retry_on_exceptions=API_RETRYABLE_EXCEPTIONS,
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


def _apply_tenacity_decorator_with_retry_if(
    func: Callable, 
    config: RetryConfiguration, 
    retry_if: Callable[[tenacity.RetryCallState], bool]
) -> Callable:
    """Internal helper to apply tenacity decorator with custom retry predicate.
    
    The retry_if predicate takes RetryCallState (tenacity 8+) and returns True to retry.
    Non-retryable exceptions are wrapped in RetryExhaustedException for consistency.
    """
    tenacity_decorator = get_tenacity_decorator(config, retry_if=retry_if)
    
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
        except Exception as e:
            # Non-retryable exception - wrap it for consistency
            raise RetryExhaustedException(
                message=f"Call failed (non-retryable)",
                attempts=1,
                last_exception=e,
            ) from e
    
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
