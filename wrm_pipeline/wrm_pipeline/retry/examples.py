"""Example usage of retry patterns.

This module provides practical examples of how to use the retry module
for S3 operations, API calls, and circuit breaker patterns.
"""

from functools import wraps
from wrm_pipeline.retry import (
    RetryConfiguration,
    CircuitBreakerConfiguration,
    CircuitBreaker,
    CircuitState,
    with_retry,
    with_s3_retry,
    with_api_retry,
    with_circuit_breaker,
    with_retry_and_circuit_breaker,
    RetryPresets,
    RetryExhaustedException,
    CircuitOpenException,
    retry_s3_upload,
    retry_s3_download,
    S3OperationHelper,
)

# =============================================================================
# Basic Retry Configuration Examples
# =============================================================================

def example_basic_retry():
    """Simple retry with default configuration."""
    config = RetryConfiguration(
        max_attempts=5,
        base_delay=1.0,
        max_delay=30.0,
        jitter=0.2,
    )
    
    @with_retry(config=config)
    def unreliable_function():
        # Your unreliable operation here
        pass
    
    return unreliable_function


def example_exponential_backoff():
    """Exponential backoff with jitter for API calls."""
    config = RetryConfiguration(
        max_attempts=5,
        base_delay=2.0,  # Start with 2 seconds
        max_delay=60.0,   # Cap at 60 seconds
        jitter=0.3,       # Add randomness (30%)
    )
    
    @with_retry(config=config)
    def fetch_from_api(endpoint):
        import requests
        response = requests.get(f"https://api.example.com/{endpoint}")
        response.raise_for_status()
        return response.json()


# =============================================================================
# S3 Retry Examples
# =============================================================================

def example_s3_upload_with_retry():
    """Upload a file to S3 with automatic retry on throttling."""
    @with_s3_retry()
    def upload_large_file(s3_client, bucket, key, body):
        return s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
        )
    
    return upload_large_file


def example_s3_download_with_retry():
    """Download from S3 with retry on transient errors."""
    @with_s3_download_retry()
    def download_data(s3_client, bucket, key):
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response['Body'].read()


def example_s3_preset_usage():
    """Use predefined retry presets for common scenarios."""
    
    # S3 Upload: Handle throttling with conservative settings
    @RetryPresets.s3_upload()
    def upload_to_s3(s3_client, bucket, key, data):
        return s3_client.put_object(Bucket=bucket, Key=key, Body=data)
    
    # S3 Download: Handle network issues with moderate settings
    @RetryPresets.s3_download()
    def download_from_s3(s3_client, bucket, key):
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response['Body'].read()
    
    # Heavy S3 Operation: More aggressive retries
    @RetryPresets.s3_heavy_operation()
    def copy_large_object(s3_client, source_bucket, source_key, dest_bucket, dest_key):
        return s3_client.copy_object(
            Bucket=dest_bucket,
            Key=dest_key,
            CopySource={'Bucket': source_bucket, 'Key': source_key},
        )


# =============================================================================
# API Retry Examples
# =============================================================================

def example_api_call_with_retry():
    """Make API calls with retry on server errors and rate limiting."""
    @with_api_retry()
    def call_external_api(url, params=None):
        import requests
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    return call_external_api


def example_api_preset_usage():
    """Use API preset for HTTP calls."""
    @RetryPresets.api_call()
    def get_data_from_api(endpoint):
        import requests
        response = requests.get(f"https://api.example.com/{endpoint}")
        response.raise_for_status()
        return response.json()


# =============================================================================
# Circuit Breaker Examples
# =============================================================================

def example_basic_circuit_breaker():
    """Wrap a function with circuit breaker protection."""
    cb_config = CircuitBreakerConfiguration(
        failure_threshold=5,      # Open after 5 failures
        recovery_timeout=60.0,    # Wait 60s before trying again
        success_threshold=3,      # Need 3 successes in HALF_OPEN to close
    )
    
    @with_circuit_breaker("api_service", config=cb_config)
    def call_external_service():
        # Your external service call
        return "success"
    
    return call_external_service


def example_circuit_breaker_state_management():
    """Monitor and manage circuit breaker state."""
    cb = CircuitBreaker(
        name="payment_service",
        config=CircuitBreakerConfiguration(
            failure_threshold=3,
            recovery_timeout=30.0,
        )
    )
    
    def payment_operation():
        # Payment processing logic
        return "payment_processed"
    
    # Execute with circuit breaker protection
    try:
        result = cb.call(payment_operation)
        print(f"Payment successful: {result}")
    except CircuitOpenException:
        print("Payment service unavailable - try again later")
    except Exception as e:
        print(f"Payment failed: {e}")
    
    # Check circuit state
    metrics = cb.get_metrics()
    print(f"Circuit state: {metrics['state']}")
    print(f"Failure count: {metrics['failure_count']}")
    print(f"Success count: {metrics['success_count']}")
    
    # Manually reset if needed
    cb.reset()


def example_combined_retry_and_circuit_breaker():
    """Combine retry logic with circuit breaker for maximum resilience."""
    
    @with_retry_and_circuit_breaker(
        retry_config=RetryConfiguration(max_attempts=3, base_delay=1.0),
        circuit_name="api_service",
        circuit_config=CircuitBreakerConfiguration(failure_threshold=5),
    )
    def call_reliable_api(endpoint):
        import requests
        response = requests.get(f"https://api.example.com/{endpoint}")
        response.raise_for_status()
        return response.json()


# =============================================================================
# S3 Operation Helper Examples
# =============================================================================

def example_s3_operation_helper():
    """Use S3OperationHelper for convenient S3 operations with retry."""
    helper = S3OperationHelper(
        bucket="my-data-bucket",
        prefix="processed/",
        retry_config=RetryConfiguration(max_attempts=5, base_delay=2.0),
    )
    
    # Upload with automatic retry
    helper.upload("data.parquet", b"parquet_data_here")
    
    # Download with automatic retry
    data = helper.download("data.parquet")
    
    # Delete with automatic retry on throttling
    helper.delete("old_data.parquet")
    
    # List objects with pagination support
    for obj in helper.list_objects(prefix="processed/"):
        print(f"Found: {obj['Key']}")


# =============================================================================
# Error Handling Examples
# =============================================================================

def example_error_handling():
    """Handle retry exhaustion and circuit breaker exceptions."""
    
    @with_retry(
        config=RetryConfiguration(max_attempts=3, base_delay=0.1),
        retry_on_exceptions=(ConnectionError, TimeoutError),
    )
    def unreliable_operation():
        raise ConnectionError("Service unavailable")
    
    try:
        result = unreliable_operation()
    except RetryExhaustedException as e:
        print(f"All retries exhausted after {e.attempts} attempts")
        print(f"Last error: {e.last_exception}")
    except Exception as e:
        print(f"Non-retryable error: {e}")


def example_circuit_breaker_error_handling():
    """Handle circuit breaker open state."""
    
    cb = CircuitBreaker(
        "external_service",
        CircuitBreakerConfiguration(failure_threshold=2, recovery_timeout=1.0),
    )
    
    def call_service():
        # Simulate service call
        raise Exception("Service error")
    
    try:
        result = cb.call(call_service)
    except CircuitOpenException:
        print("Circuit is OPEN - service unavailable")
        metrics = cb.get_metrics()
        print(f"Time until retry: {metrics['time_until_retry']:.1f}s")
    except Exception as e:
        print(f"Operation failed: {e}")


# =============================================================================
# Advanced Examples
# =============================================================================

def example_custom_retry_predicate():
    """Create custom retry decision logic."""
    def should_retry(state):
        """Custom retry logic based on exception type and attempt count."""
        exception = state.outcome.exception()
        
        if isinstance(exception, (ConnectionError, TimeoutError)):
            return True  # Retry on network errors
        
        if isinstance(exception, ValueError):
            return state.attempt_number < 2  # Only retry ValueError twice
        
        return False  # Don't retry other errors
    
    config = RetryConfiguration(max_attempts=5, base_delay=1.0)
    
    @with_retry(config=config, retry_if=should_retry)
    def custom_retry_operation():
        # Your operation
        pass


def example_monitoring_metrics():
    """Extract metrics for monitoring and alerting."""
    
    cb = CircuitBreaker(
        "critical_service",
        CircuitBreakerConfiguration(failure_threshold=10),
    )
    
    def service_call():
        return "success"
    
    # Execute operation
    try:
        cb.call(service_call)
    except CircuitOpenException:
        pass
    
    # Get metrics for monitoring
    metrics = cb.get_metrics()
    
    # Alert if circuit is open or failure rate is high
    if metrics['is_open']:
        print("ALERT: Circuit is OPEN - service degraded")
    
    if metrics['failure_count'] > 5:
        print(f"WARNING: High failure count: {metrics['failure_count']}")


if __name__ == "__main__":
    # Run examples
    print("Running retry module examples...")
    
    # Example: Error handling
    print("\n=== Error Handling Example ===")
    try:
        example_error_handling()
    except Exception:
        pass
    
    print("\nExamples completed successfully!")
