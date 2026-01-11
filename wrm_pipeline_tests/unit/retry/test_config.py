"""Tests for retry configuration and tenacity integration."""

import pytest
from unittest.mock import Mock, patch
import time

from wrm_pipeline.retry import (
    RetryConfiguration,
    with_retry,
)


class TestRetryConfiguration:
    """Test RetryConfiguration dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = RetryConfiguration()
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.exponential_base == 2.0
        assert config.jitter == 0.1
        assert config.respect_retry_after is True
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = RetryConfiguration(
            max_attempts=5,
            base_delay=0.5,
            max_delay=30.0,
            exponential_base=3.0,
            jitter=0.2,
        )
        assert config.max_attempts == 5
        assert config.base_delay == 0.5
        assert config.max_delay == 30.0
        assert config.exponential_base == 3.0
        assert config.jitter == 0.2
    
    def test_validation_max_attempts(self):
        """Test validation for max_attempts."""
        with pytest.raises(ValueError):
            RetryConfiguration(max_attempts=0)
    
    def test_validation_base_delay(self):
        """Test validation for base_delay."""
        with pytest.raises(ValueError):
            RetryConfiguration(base_delay=0)
    
    def test_validation_max_delay(self):
        """Test validation for max_delay."""
        with pytest.raises(ValueError):
            RetryConfiguration(max_delay=-1)


class TestWithRetryDecorator:
    """Test with_retry decorator."""
    
    def test_successful_function(self):
        """Test that successful functions work without retry."""
        call_count = 0
        
        @with_retry(max_attempts=3)
        def success():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = success()
        assert result == "success"
        assert call_count == 1
    
    def test_retry_on_exception(self):
        """Test that retries occur on exceptions."""
        call_count = 0
        
        @with_retry(max_attempts=3, base_delay=0.01)
        def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary failure")
            return "success"
        
        result = fail_twice()
        assert result == "success"
        assert call_count == 3
    
    def test_exhausted_retries_raises_exception(self):
        """Test that RetryExhaustedException is raised after all retries."""
        @with_retry(max_attempts=3, base_delay=0.01)
        def always_fail():
            raise ValueError("Always fails")
        
        from wrm_pipeline.retry import RetryExhaustedException
        with pytest.raises(RetryExhaustedException) as exc_info:
            always_fail()
        
        assert exc_info.value.attempts == 3
