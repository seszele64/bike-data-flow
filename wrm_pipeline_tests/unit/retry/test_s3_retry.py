"""Tests for S3 retry decorator."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError, HTTPClientError

from wrm_pipeline.retry import (
    with_s3_retry,
    with_s3_upload_retry,
    with_s3_download_retry,
    RetryPresets,
    S3_NON_RETRYABLE_ERROR_CODES,
    S3_RETRYABLE_ERROR_CODES,
    is_s3_retryable_error,
    S3_RETRYABLE_EXCEPTIONS,
    RetryExhaustedException,
)


class TestS3RetryDecorator:
    """Test with_s3_retry decorator."""
    
    def test_successful_s3_operation(self):
        """Test that successful S3 operations work without retry."""
        call_count = 0
        
        @with_s3_retry(max_attempts=3)
        def s3_operation():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = s3_operation()
        assert result == "success"
        assert call_count == 1
    
    def test_retry_on_retryable_error(self):
        """Test retry on retryable S3 error (InternalError)."""
        call_count = 0
        
        @with_s3_retry(max_attempts=3, base_delay=0.01)
        def s3_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                error = Mock()
                error.response = {"Error": {"Code": "InternalError"}}
                raise ClientError(error.response, "GetObject")
            return "success"
        
        result = s3_operation()
        assert result == "success"
        assert call_count == 3
    
    def test_retry_on_throttling_error(self):
        """Test retry on Throttling error code."""
        call_count = 0
        
        @with_s3_retry(max_attempts=3, base_delay=0.01)
        def s3_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                error = Mock()
                error.response = {"Error": {"Code": "Throttling"}}
                raise ClientError(error.response, "ListBuckets")
            return "success"
        
        result = s3_operation()
        assert result == "success"
        assert call_count == 2
    
    def test_retry_on_slow_down_error(self):
        """Test retry on SlowDown error code."""
        call_count = 0
        
        @with_s3_retry(max_attempts=3, base_delay=0.01)
        def s3_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                error = Mock()
                error.response = {"Error": {"Code": "SlowDown"}}
                raise ClientError(error.response, "PutObject")
            return "success"
        
        result = s3_operation()
        assert result == "success"
        assert call_count == 2
    
    def test_5xx_errors_are_retryable(self):
        """Test that 5xx HTTP errors are retryable."""
        call_count = 0
        
        @with_s3_retry(max_attempts=3, base_delay=0.01)
        def s3_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                error = Mock()
                error.response = {"Error": {"Code": "500"}}
                raise ClientError(error.response, "GetObject")
            return "success"
        
        result = s3_operation()
        assert result == "success"
        assert call_count == 2
    
    def test_custom_parameters(self):
        """Test that custom parameters are applied correctly."""
        @with_s3_retry(max_attempts=7, base_delay=2.0, max_delay=60.0, jitter=3.0)
        def s3_operation():
            return "success"
        
        result = s3_operation()
        assert result == "success"
    
    def test_function_arguments_preserved(self):
        """Test that function arguments are preserved through decorator."""
        @with_s3_retry(max_attempts=3)
        def s3_operation(bucket, key, data=None):
            return f"{bucket}/{key}"
        
        result = s3_operation("my-bucket", "my-key", data="test")
        assert result == "my-bucket/my-key"


class TestS3ErrorFiltering:
    """Test S3 error code filtering."""
    
    def test_non_retryable_error_codes_are_defined(self):
        """Test that non-retryable error codes are defined."""
        assert "AccessDenied" in S3_NON_RETRYABLE_ERROR_CODES
        assert "NoSuchKey" in S3_NON_RETRYABLE_ERROR_CODES
        assert "NoSuchBucket" in S3_NON_RETRYABLE_ERROR_CODES
        assert "InvalidObjectState" in S3_NON_RETRYABLE_ERROR_CODES
    
    def test_retryable_error_codes_are_defined(self):
        """Test that retryable error codes are defined."""
        assert "Throttling" in S3_RETRYABLE_ERROR_CODES
        assert "InternalError" in S3_RETRYABLE_ERROR_CODES
        assert "ServiceUnavailable" in S3_RETRYABLE_ERROR_CODES
        assert "SlowDown" in S3_RETRYABLE_ERROR_CODES
    
    def test_non_retryable_errors_return_false(self):
        """Test that non-retryable error codes return False."""
        non_retryable_codes = [
            "AccessDenied",
            "NoSuchKey",
            "NoSuchBucket",
            "InvalidObjectState",
            "SignatureDoesNotMatch",
            "InvalidAccessKeyId",
        ]
        
        for code in non_retryable_codes:
            mock_error = Mock()
            mock_error.response = {"Error": {"Code": code}}
            assert is_s3_retryable_error(mock_error) is False, f"{code} should be non-retryable"
    
    def test_retryable_errors_return_true(self):
        """Test that retryable error codes return True."""
        retryable_codes = [
            "Throttling",
            "ThrottlingException",
            "InternalError",
            "ServiceUnavailable",
            "ServiceUnavailableException",
            "SlowDown",
            "ProvisionedThroughputExceededException",
            "RequestTimeout",
        ]
        
        for code in retryable_codes:
            mock_error = Mock()
            mock_error.response = {"Error": {"Code": code}}
            assert is_s3_retryable_error(mock_error) is True, f"{code} should be retryable"
    
    def test_5xx_errors_are_retryable(self):
        """Test that 5xx HTTP errors are retryable."""
        for code in ["500", "502", "503", "504"]:
            mock_error = Mock()
            mock_error.response = {"Error": {"Code": code}}
            assert is_s3_retryable_error(mock_error) is True, f"{code} should be retryable"
    
    def test_unknown_error_codes_are_not_retryable(self):
        """Test that unknown error codes are not retryable."""
        mock_error = Mock()
        mock_error.response = {"Error": {"Code": "SomeUnknownError"}}
        assert is_s3_retryable_error(mock_error) is False
    
    def test_missing_error_code_is_not_retryable(self):
        """Test that missing error code is not retryable."""
        mock_error = Mock()
        mock_error.response = {"Error": {}}
        assert is_s3_retryable_error(mock_error) is False


class TestRetryPresets:
    """Test RetryPresets configurations."""
    
    def test_s3_upload_preset(self):
        """Test S3_UPLOAD preset configuration."""
        preset = RetryPresets.S3_UPLOAD
        assert preset.max_attempts == 5
        assert preset.base_delay == 1.0
        assert preset.max_delay == 30.0
        assert preset.jitter == 1.0
    
    def test_s3_download_preset(self):
        """Test S3_DOWNLOAD preset configuration."""
        preset = RetryPresets.S3_DOWNLOAD
        assert preset.max_attempts == 5
        assert preset.base_delay == 0.5
        assert preset.max_delay == 30.0
        assert preset.jitter == 0.5
    
    def test_s3_heavy_operation_preset(self):
        """Test S3_HEAVY_OPERATION preset configuration."""
        preset = RetryPresets.S3_HEAVY_OPERATION
        assert preset.max_attempts == 3
        assert preset.base_delay == 2.0
        assert preset.max_delay == 60.0
        assert preset.jitter == 2.0
    
    def test_api_call_preset(self):
        """Test API_CALL preset configuration."""
        preset = RetryPresets.API_CALL
        assert preset.max_attempts == 3
        assert preset.base_delay == 0.5
        assert preset.max_delay == 10.0
        assert preset.jitter == 0.5


class TestS3ConvenienceDecorators:
    """Test S3 convenience decorators (with_s3_upload_retry, with_s3_download_retry)."""
    
    def test_with_s3_upload_retry_success(self):
        """Test with_s3_upload_retry decorator success."""
        @with_s3_upload_retry
        def upload_to_s3(bucket, key):
            return f"uploaded {bucket}/{key}"
        
        result = upload_to_s3("my-bucket", "my-key")
        assert result == "uploaded my-bucket/my-key"
    
    def test_with_s3_download_retry_success(self):
        """Test with_s3_download_retry decorator success."""
        @with_s3_download_retry
        def download_from_s3(bucket, key):
            return f"downloaded {bucket}/{key}"
        
        result = download_from_s3("my-bucket", "my-key")
        assert result == "downloaded my-bucket/my-key"
    
    def test_with_s3_upload_retry_retries(self):
        """Test with_s3_upload_retry retries on error."""
        call_count = 0
        
        @with_s3_upload_retry
        def upload_to_s3(bucket, key):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                error = Mock()
                error.response = {"Error": {"Code": "InternalError"}}
                raise ClientError(error.response, "PutObject")
            return f"uploaded {bucket}/{key}"
        
        result = upload_to_s3("my-bucket", "my-key")
        assert result == "uploaded my-bucket/my-key"
        assert call_count == 2


class TestS3RetryableExceptions:
    """Test S3_RETRYABLE_EXCEPTIONS tuple."""
    
    def test_client_error_in_retryable_exceptions(self):
        """Test that ClientError is in retryable exceptions."""
        assert ClientError in S3_RETRYABLE_EXCEPTIONS
    
    def test_http_client_error_in_retryable_exceptions(self):
        """Test that HTTPClientError is in retryable exceptions."""
        assert HTTPClientError in S3_RETRYABLE_EXCEPTIONS


class TestRetryExhaustedException:
    """Test RetryExhaustedException in S3 context."""
    
    def test_retry_exhausted_contains_attempts(self):
        """Test that RetryExhaustedException contains attempt count."""
        original_error = Mock()
        original_error.response = {"Error": {"Code": "InternalError"}}
        
        exc = RetryExhaustedException(
            message="All 3 S3 retry attempts exhausted",
            attempts=3,
            last_exception=ClientError(original_error.response, "GetObject"),
        )
        
        assert exc.attempts == 3
        assert "3" in str(exc)
    
    def test_retry_exhausted_preserves_last_exception(self):
        """Test that RetryExhaustedException preserves last exception."""
        original_error = Mock()
        original_error.response = {"Error": {"Code": "InternalError"}}
        client_error = ClientError(original_error.response, "GetObject")
        
        exc = RetryExhaustedException(
            message="All retry attempts exhausted",
            attempts=5,
            last_exception=client_error,
        )
        
        assert exc.last_exception is client_error


class TestIntegration:
    """Integration tests for S3 retry with various scenarios."""
    
    def test_full_retry_cycle_success_after_retries(self):
        """Test full retry cycle where operation succeeds after multiple retries."""
        call_count = 0
        
        @with_s3_retry(max_attempts=5, base_delay=0.01)
        def unreliable_s3_operation():
            nonlocal call_count
            call_count += 1
            
            # Fail first 3 times, then succeed
            if call_count <= 3:
                error = Mock()
                error.response = {"Error": {"Code": "InternalError"}}
                raise ClientError(error.response, "GetObject")
            return f"success on attempt {call_count}"
        
        result = unreliable_s3_operation()
        assert "attempt 4" in result
        assert call_count == 4
    
    def test_max_retries_exceeded(self):
        """Test that RetryExhaustedException is raised when max retries exceeded."""
        @with_s3_retry(max_attempts=3, base_delay=0.01)
        def always_failing_operation():
            error = Mock()
            error.response = {"Error": {"Code": "InternalError"}}
            raise ClientError(error.response, "GetObject")
        
        with pytest.raises(RetryExhaustedException) as exc_info:
            always_failing_operation()
        
        assert exc_info.value.attempts == 3
