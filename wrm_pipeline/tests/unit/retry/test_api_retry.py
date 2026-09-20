"""Tests for API retry operations."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests
from requests.exceptions import ConnectionError, Timeout, HTTPError, RequestException

from wrm_pipeline.retry import (
    with_api_retry,
    with_api_call_retry,
    RetryPresets,
    API_NON_RETRYABLE_STATUS_CODES,
    API_RETRYABLE_EXCEPTIONS,
    RetryAfterWaitStrategy,
    is_api_retryable_error,
)


class TestAPIRetryDecorator:
    """Test with_api_retry decorator."""

    def test_successful_api_call(self):
        """Test that successful API calls work without retry."""
        call_count = 0

        @with_api_retry(max_attempts=3)
        def api_call():
            nonlocal call_count
            call_count += 1
            return {"data": "success"}

        result = api_call()
        assert result == {"data": "success"}
        assert call_count == 1

    def test_retry_on_connection_error(self):
        """Test retry on ConnectionError."""
        call_count = 0

        @with_api_retry(max_attempts=3, base_delay=0.01)
        def api_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection failed")
            return {"data": "success"}

        result = api_call()
        assert result == {"data": "success"}
        assert call_count == 3

    def test_retry_on_timeout(self):
        """Test retry on Timeout."""
        call_count = 0

        @with_api_retry(max_attempts=3, base_delay=0.01)
        def api_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Timeout("Request timed out")
            return {"data": "success"}

        result = api_call()
        assert result == {"data": "success"}
        assert call_count == 3

    def test_retry_on_503(self):
        """Test retry on 503 Service Unavailable."""
        call_count = 0

        @with_api_retry(max_attempts=3, base_delay=0.01)
        def api_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                mock_response = Mock()
                mock_response.status_code = 503
                mock_response.headers = {}
                raise HTTPError("Service Unavailable", response=mock_response)
            return {"data": "success"}

        result = api_call()
        assert result == {"data": "success"}
        assert call_count == 3

    def test_no_retry_on_404(self):
        """Test that 404 raises immediately."""
        @with_api_retry(max_attempts=3)
        def api_call():
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.headers = {}
            raise HTTPError("Not Found", response=mock_response)

        from wrm_pipeline.retry import RetryExhaustedException
        with pytest.raises(RetryExhaustedException) as exc_info:
            api_call()
        # Should fail on first attempt (not retry)
        assert exc_info.value.attempts == 1

    def test_no_retry_on_401(self):
        """Test that 401 raises immediately."""
        @with_api_retry(max_attempts=3)
        def api_call():
            mock_response = Mock()
            mock_response.status_code = 401
            mock_response.headers = {}
            raise HTTPError("Unauthorized", response=mock_response)

        from wrm_pipeline.retry import RetryExhaustedException
        with pytest.raises(RetryExhaustedException) as exc_info:
            api_call()
        assert exc_info.value.attempts == 1

    def test_retry_on_429(self):
        """Test retry on 429 Rate Limit."""
        call_count = 0

        @with_api_retry(max_attempts=3, base_delay=0.01)
        def api_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                mock_response = Mock()
                mock_response.status_code = 429
                mock_response.headers = {}
                raise HTTPError("Too Many Requests", response=mock_response)
            return {"data": "success"}

        result = api_call()
        assert result == {"data": "success"}
        assert call_count == 3

    def test_retry_on_500(self):
        """Test retry on 500 Internal Server Error."""
        call_count = 0

        @with_api_retry(max_attempts=3, base_delay=0.01)
        def api_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                mock_response = Mock()
                mock_response.status_code = 500
                mock_response.headers = {}
                raise HTTPError("Internal Server Error", response=mock_response)
            return {"data": "success"}

        result = api_call()
        assert result == {"data": "success"}
        assert call_count == 3

    def test_exhausted_retries_raises_exception(self):
        """Test that exhausted retries raises RetryExhaustedException."""
        @with_api_retry(max_attempts=3, base_delay=0.01)
        def api_call():
            raise ConnectionError("Always fails")

        from wrm_pipeline.retry import RetryExhaustedException
        with pytest.raises(RetryExhaustedException) as exc_info:
            api_call()
        assert exc_info.value.attempts == 3
        assert "All 3 API retry attempts exhausted" in str(exc_info.value)


class TestRetryAfterHeader:
    """Test Retry-After header handling."""

    def test_parse_retry_after_seconds(self):
        """Test parsing Retry-After as seconds."""
        strategy = RetryAfterWaitStrategy(base_delay=0.5, max_delay=60.0)

        mock_response = Mock()
        mock_response.headers = {'Retry-After': '30'}
        mock_exception = HTTPError("Too Many Requests", response=mock_response)

        retry_state = self._create_mock_retry_state(mock_exception, 2)
        wait_time = strategy(retry_state)

        assert wait_time == 30.0

    def test_parse_retry_after_http_date(self):
        """Test parsing Retry-After as HTTP-date."""
        from datetime import datetime, timedelta, timezone

        strategy = RetryAfterWaitStrategy(base_delay=0.5, max_delay=60.0)

        # Use UTC time to avoid timezone issues
        future = datetime.now(timezone.utc) + timedelta(seconds=5)
        http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")

        mock_response = Mock()
        mock_response.headers = {'Retry-After': http_date}
        mock_exception = HTTPError("Too Many Requests", response=mock_response)

        retry_state = self._create_mock_retry_state(mock_exception, 2)
        wait_time = strategy(retry_state)

        # Should be approximately 5 seconds (widen tolerance for timing variance)
        assert 4.0 <= wait_time <= 5.5

    def test_fallback_to_exponential_backoff(self):
        """Test fallback to exponential backoff when no Retry-After header."""
        strategy = RetryAfterWaitStrategy(base_delay=0.5, max_delay=10.0)

        mock_response = Mock()
        mock_response.headers = {}
        mock_exception = HTTPError("Service Unavailable", response=mock_response)

        retry_state = self._create_mock_retry_state(mock_exception, 3)
        wait_time = strategy(retry_state)

        # Exponential backoff: 0.5 * 2^(3-1) = 0.5 * 4 = 2.0, plus jitter
        expected_base = 0.5 * (2 ** (3 - 1))
        assert expected_base <= wait_time <= expected_base + 0.5

    def test_respects_max_delay(self):
        """Test that max_delay cap is respected."""
        strategy = RetryAfterWaitStrategy(base_delay=0.5, max_delay=10.0)

        mock_response = Mock()
        # Request 60 seconds, but should be capped at 10
        mock_response.headers = {'Retry-After': '60'}
        mock_exception = HTTPError("Too Many Requests", response=mock_response)

        retry_state = self._create_mock_retry_state(mock_exception, 2)
        wait_time = strategy(retry_state)

        assert wait_time == 10.0

    def test_case_insensitive_header(self):
        """Test that Retry-After header is case-insensitive."""
        strategy = RetryAfterWaitStrategy(base_delay=0.5, max_delay=60.0)

        mock_response = Mock()
        mock_response.headers = {'retry-after': '25'}
        mock_exception = HTTPError("Too Many Requests", response=mock_response)

        retry_state = self._create_mock_retry_state(mock_exception, 2)
        wait_time = strategy(retry_state)

        assert wait_time == 25.0

    def _create_mock_retry_state(self, exception, attempt):
        """Helper to create mock retry state."""
        outcome = Mock()
        outcome.exception = Mock(return_value=exception)

        next_action = Mock()
        next_action.next_attempt = attempt

        retry_state = Mock()
        retry_state.outcome = outcome
        retry_state.next_action = next_action

        return retry_state


class TestRetryPresetsAPI:
    """Test API-related retry presets."""

    def test_api_call_preset(self):
        """Test API_CALL preset configuration."""
        preset = RetryPresets.API_CALL
        assert preset.max_attempts == 3
        assert preset.base_delay == 0.5
        assert preset.max_delay == 10.0
        assert preset.jitter == 0.5
        assert ConnectionError in preset.retry_on_exceptions
        assert Timeout in preset.retry_on_exceptions
        assert HTTPError in preset.retry_on_exceptions

    def test_non_retryable_4xx_codes(self):
        """Test that 4xx codes (except 429) are non-retryable."""
        non_retryable = [400, 401, 403, 404, 405, 409, 422]
        for code in non_retryable:
            assert code in API_NON_RETRYABLE_STATUS_CODES

    def test_429_is_retryable(self):
        """Test that 429 (rate limit) is retryable."""
        assert 429 not in API_NON_RETRYABLE_STATUS_CODES

    def test_5xx_codes_are_retryable(self):
        """Test that 5xx codes are retryable via default behavior."""
        # 5xx codes are not in non-retryable list
        for code in [500, 502, 503, 504]:
            assert code not in API_NON_RETRYABLE_STATUS_CODES


class TestIsApiRetryableError:
    """Test is_api_retryable_error function."""

    def test_429_is_retryable(self):
        """Test that 429 is retryable."""
        mock_response = Mock()
        mock_response.status_code = 429
        exception = HTTPError("Too Many Requests", response=mock_response)

        assert is_api_retryable_error(exception) is True

    def test_500_is_retryable(self):
        """Test that 500 is retryable."""
        mock_response = Mock()
        mock_response.status_code = 500
        exception = HTTPError("Internal Server Error", response=mock_response)

        assert is_api_retryable_error(exception) is True

    def test_503_is_retryable(self):
        """Test that 503 is retryable."""
        mock_response = Mock()
        mock_response.status_code = 503
        exception = HTTPError("Service Unavailable", response=mock_response)

        assert is_api_retryable_error(exception) is True

    def test_404_not_retryable(self):
        """Test that 404 is not retryable."""
        mock_response = Mock()
        mock_response.status_code = 404
        exception = HTTPError("Not Found", response=mock_response)

        assert is_api_retryable_error(exception) is False

    def test_401_not_retryable(self):
        """Test that 401 is not retryable."""
        mock_response = Mock()
        mock_response.status_code = 401
        exception = HTTPError("Unauthorized", response=mock_response)

        assert is_api_retryable_error(exception) is False

    def test_400_not_retryable(self):
        """Test that 400 is not retryable."""
        mock_response = Mock()
        mock_response.status_code = 400
        exception = HTTPError("Bad Request", response=mock_response)

        assert is_api_retryable_error(exception) is False


class TestWithApiCallRetry:
    """Test with_api_call_retry decorator."""

    def test_successful_call(self):
        """Test successful API call without retry."""
        call_count = 0

        @with_api_call_retry()
        def api_call():
            nonlocal call_count
            call_count += 1
            return {"status": "ok"}

        result = api_call()
        assert result == {"status": "ok"}
        assert call_count == 1

    def test_retry_on_connection_error(self):
        """Test retry on connection error."""
        call_count = 0

        @with_api_call_retry()
        def api_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection failed")
            return {"status": "ok"}

        result = api_call()
        assert result == {"status": "ok"}
        assert call_count == 3


class TestApiRetryableExceptions:
    """Test API retryable exceptions tuple."""

    def test_contains_connection_error(self):
        """Test that ConnectionError is retryable."""
        assert ConnectionError in API_RETRYABLE_EXCEPTIONS

    def test_contains_timeout(self):
        """Test that Timeout is retryable."""
        assert Timeout in API_RETRYABLE_EXCEPTIONS

    def test_contains_http_error(self):
        """Test that HTTPError is retryable."""
        assert HTTPError in API_RETRYABLE_EXCEPTIONS

    def test_contains_request_exception(self):
        """Test that RequestException is retryable."""
        assert RequestException in API_RETRYABLE_EXCEPTIONS
