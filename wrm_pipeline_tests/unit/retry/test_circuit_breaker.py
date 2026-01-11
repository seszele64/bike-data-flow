"""Tests for Circuit Breaker implementation."""

import pytest
import time
from unittest.mock import Mock, patch
from wrm_pipeline.retry import (
    CircuitBreaker,
    CircuitState,
    CircuitOpenException,
    CircuitBreakerConfiguration,
    with_circuit_breaker,
    with_retry_and_circuit_breaker,
    get_circuit_breaker,
)


class TestCircuitBreakerInitialization:
    """Test circuit breaker initialization."""
    
    def test_initial_state_is_closed(self):
        """Verify circuit starts in CLOSED state."""
        cb = CircuitBreaker("test", CircuitBreakerConfiguration())
        assert cb.state == CircuitState.CLOSED
    
    def test_initial_failure_count_is_zero(self):
        """Verify initial failure count is 0."""
        cb = CircuitBreaker("test", CircuitBreakerConfiguration(failure_threshold=3))
        assert cb.failure_count == 0
    
    def test_initial_success_count_is_zero(self):
        """Verify initial success count is 0."""
        cb = CircuitBreaker("test", CircuitBreakerConfiguration())
        assert cb.success_count == 0
    
    def test_default_configuration(self):
        """Test default configuration values."""
        cb = CircuitBreaker("test")
        assert cb.config.failure_threshold == 5
        assert cb.config.recovery_timeout == 60.0
    
    def test_custom_configuration(self):
        """Test custom configuration values."""
        config = CircuitBreakerConfiguration(failure_threshold=3, recovery_timeout=30.0)
        cb = CircuitBreaker("test", config)
        assert cb.config.failure_threshold == 3
        assert cb.config.recovery_timeout == 30.0
    
    def test_repr(self):
        """Test string representation."""
        cb = CircuitBreaker("test", CircuitBreakerConfiguration(failure_threshold=5))
        repr_str = repr(cb)
        assert "CircuitBreaker" in repr_str
        assert "test" in repr_str
        assert "CLOSED" in repr_str


class TestCircuitBreakerStateTransitions:
    """Test state transitions."""
    
    def test_closed_to_open_on_failure_threshold(self):
        """Test transition from CLOSED to OPEN after threshold failures."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=3, recovery_timeout=1.0)
        )
        
        # Simulate failures
        for i in range(3):
            cb._on_failure(Exception(f"Failure {i+1}"))
        
        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3
    
    def test_open_rejects_requests(self):
        """Test that OPEN state rejects requests immediately."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=60.0)
        )
        
        # Force to OPEN state
        cb._on_failure(Exception("Failure"))
        assert cb.state == CircuitState.OPEN
        
        # Should raise CircuitOpenException
        with pytest.raises(CircuitOpenException):
            cb._update_state_for_attempt()
    
    def test_open_to_half_open_after_timeout(self):
        """Test transition from OPEN to HALF_OPEN after recovery timeout."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=0.1)
        )
        
        # Force to OPEN state
        cb._on_failure(Exception("Failure"))
        assert cb.state == CircuitState.OPEN
        
        # Wait for recovery timeout
        time.sleep(0.15)
        
        # Next attempt should transition to HALF_OPEN
        cb._update_state_for_attempt()
        assert cb.state == CircuitState.HALF_OPEN
    
    def test_half_open_to_closed_on_success(self):
        """Test transition from HALF_OPEN to CLOSED on success."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=0.1)
        )
        
        # Force to HALF_OPEN state
        cb._state = CircuitState.HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN
        
        # Success should transition to CLOSED
        cb._on_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
    
    def test_half_open_to_open_on_failure(self):
        """Test transition from HALF_OPEN back to OPEN on failure."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=0.1)
        )
        
        # Force to HALF_OPEN state
        cb._state = CircuitState.HALF_OPEN
        
        # Failure should transition back to OPEN
        cb._on_failure(Exception("Failure"))
        assert cb.state == CircuitState.OPEN
    
    def test_closed_resets_failure_count_on_success(self):
        """Test that success resets failure count in CLOSED state."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=3)
        )
        
        # Add some failures
        cb._on_failure(Exception("Failure 1"))
        cb._on_failure(Exception("Failure 2"))
        assert cb.failure_count == 2
        
        # Success should reset failure count
        cb._on_success()
        assert cb.failure_count == 0
    
    def test_failure_count_not_exceed_threshold(self):
        """Test that failure count doesn't exceed threshold."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=2)
        )
        
        # Add more failures than threshold
        cb._on_failure(Exception("Failure 1"))
        cb._on_failure(Exception("Failure 2"))
        cb._on_failure(Exception("Failure 3"))
        
        # State should be OPEN and count should equal threshold
        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 2


class TestCircuitBreakerCall:
    """Test the call() method."""
    
    def test_successful_call(self):
        """Test successful function call through circuit breaker."""
        cb = CircuitBreaker("test")
        
        result = cb.call(lambda: "success")
        assert result == "success"
        assert cb.state == CircuitState.CLOSED
    
    def test_successful_call_with_args(self):
        """Test successful function call with arguments."""
        cb = CircuitBreaker("test")
        
        def add(a, b):
            return a + b
        
        result = cb.call(add, 1, 2)
        assert result == 3
    
    def test_successful_call_with_kwargs(self):
        """Test successful function call with keyword arguments."""
        cb = CircuitBreaker("test")
        
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"
        
        result = cb.call(greet, "World", greeting="Hi")
        assert result == "Hi, World!"
    
    def test_call_failure_tracks_failure(self):
        """Test that failures are tracked."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=2)
        )
        
        def failing_func():
            raise ValueError("Test error")
        
        with pytest.raises(ValueError):
            cb.call(failing_func)
        
        assert cb.failure_count == 1
    
    def test_call_when_open_raises_exception(self):
        """Test that calls raise CircuitOpenException when open."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=60.0)
        )
        cb._on_failure(Exception("Failure"))  # Force open
        
        with pytest.raises(CircuitOpenException):
            cb.call(lambda: "should not reach here")
    
    def test_call_propagates_exception(self):
        """Test that call propagates original exception."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=2)
        )
        
        original_exception = ValueError("Original error")
        
        with pytest.raises(ValueError, match="Original error"):
            cb.call(lambda: (_ for _ in ()).throw(original_exception))
    
    def test_multiple_successive_failures(self):
        """Test multiple successive failures."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=3)
        )
        
        call_count = 0
        
        def sometimes_fails():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection failed")
            return "success"
        
        # First two calls fail
        with pytest.raises(ConnectionError):
            cb.call(sometimes_fails)
        with pytest.raises(ConnectionError):
            cb.call(sometimes_fails)
        
        assert cb.failure_count == 2
        assert cb.state == CircuitState.CLOSED
        
        # Third call succeeds
        result = cb.call(sometimes_fails)
        assert result == "success"
        assert cb.failure_count == 0


class TestCircuitBreakerContextManager:
    """Test context manager functionality."""
    
    def test_context_manager_success(self):
        """Test successful context manager usage."""
        cb = CircuitBreaker("test")
        
        with cb.context():
            result = 1 + 1
        assert result == 2
        assert cb.state == CircuitState.CLOSED
    
    def test_context_manager_failure(self):
        """Test context manager with failure."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=2)
        )
        
        with pytest.raises(ValueError):
            with cb.context():
                raise ValueError("Test error")
        
        assert cb.failure_count == 1
    
    def test_context_manager_reset_on_success(self):
        """Test context manager resets failure count on success."""
        cb = CircuitBreaker("test")
        
        # Simulate some failures first
        cb._failure_count = 2
        
        with cb.context():
            pass  # Success
        
        assert cb.failure_count == 0
    
    def test_context_manager_enter_exit(self):
        """Test __enter__ and __exit__ methods."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=2)
        )
        
        # Test __enter__
        with cb:
            result = cb.call(lambda: "test")
            assert result == "test"
        
        # Should not raise and state should be CLOSED
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


class TestCircuitBreakerDecorator:
    """Test circuit breaker decorator."""
    
    def test_decorator_creates_circuit_breaker(self):
        """Test that decorator creates and uses a circuit breaker."""
        call_count = 0
        
        @with_circuit_breaker(name="test-decorator", failure_threshold=3)
        def test_func():
            nonlocal call_count
            call_count += 1
            return call_count
        
        result = test_func()
        assert result == 1
        
        # Get the circuit breaker and verify it's in CLOSED state
        cb = get_circuit_breaker("test-decorator")
        assert cb.state == CircuitState.CLOSED
    
    def test_decorator_tracks_failures(self):
        """Test that decorator tracks failures."""
        @with_circuit_breaker(name="test-failures", failure_threshold=2)
        def failing_func():
            raise ValueError("Always fails")
        
        with pytest.raises(ValueError):
            failing_func()
        
        cb = get_circuit_breaker("test-failures")
        assert cb.failure_count == 1
    
    def test_decorator_opens_circuit_on_threshold(self):
        """Test that decorator opens circuit after threshold failures."""
        @with_circuit_breaker(name="test-threshold", failure_threshold=2, recovery_timeout=60.0)
        def always_fails():
            raise ValueError("Always fails")
        
        # First failure
        with pytest.raises(ValueError):
            always_fails()
        
        # Second failure - should open circuit
        with pytest.raises(ValueError):
            always_fails()
        
        cb = get_circuit_breaker("test-threshold")
        assert cb.state == CircuitState.OPEN
        
        # Next call should raise CircuitOpenException
        with pytest.raises(CircuitOpenException):
            always_fails()
    
    def test_decorator_with_args_kwargs(self):
        """Test decorator with function that has args and kwargs."""
        @with_circuit_breaker(name="test-args")
        def compute(a, b, multiplier=1):
            return (a + b) * multiplier
        
        result = compute(2, 3, multiplier=2)
        assert result == 10


class TestCircuitBreakerMetrics:
    """Test metrics functionality."""
    
    def test_get_metrics(self):
        """Test metrics are returned correctly."""
        cb = CircuitBreaker(
            "test-metrics",
            CircuitBreakerConfiguration(failure_threshold=5, recovery_timeout=30.0)
        )
        
        metrics = cb.get_metrics()
        
        assert metrics["name"] == "test-metrics"
        assert metrics["state"] == "CLOSED"
        assert metrics["failure_count"] == 0
        assert metrics["success_count"] == 0
        assert metrics["failure_threshold"] == 5
        assert metrics["recovery_timeout"] == 30.0
        assert metrics["is_closed"] is True
        assert metrics["is_open"] is False
        assert metrics["is_half_open"] is False
        assert metrics["time_until_retry"] == 0
    
    def test_metrics_reflect_open_state(self):
        """Test metrics reflect OPEN state."""
        cb = CircuitBreaker(
            "test-metrics",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=60.0)
        )
        cb._on_failure(Exception("Failure"))
        
        metrics = cb.get_metrics()
        
        assert metrics["state"] == "OPEN"
        assert metrics["is_open"] is True
        assert metrics["failure_count"] == 1
    
    def test_metrics_reflect_half_open_state(self):
        """Test metrics reflect HALF_OPEN state."""
        cb = CircuitBreaker(
            "test-metrics",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=0.1)
        )
        cb._on_failure(Exception("Failure"))
        
        # Wait for recovery timeout
        time.sleep(0.15)
        cb._update_state_for_attempt()
        
        metrics = cb.get_metrics()
        
        assert metrics["state"] == "HALF_OPEN"
        assert metrics["is_half_open"] is True
    
    def test_metrics_time_until_retry(self):
        """Test time_until_retry is calculated correctly."""
        cb = CircuitBreaker(
            "test-metrics",
            CircuitBreakerConfiguration(failure_threshold=1, recovery_timeout=1.0)
        )
        cb._on_failure(Exception("Failure"))
        
        metrics = cb.get_metrics()
        
        assert metrics["is_open"] is True
        assert metrics["time_until_retry"] > 0
        assert metrics["time_until_retry"] <= 1.0


class TestCircuitBreakerReset:
    """Test reset functionality."""
    
    def test_reset_to_closed(self):
        """Test reset returns circuit to CLOSED state."""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfiguration(failure_threshold=2)
        )
        
        # Add some failures
        cb._on_failure(Exception("Failure 1"))
        cb._on_failure(Exception("Failure 2"))
        assert cb.state == CircuitState.OPEN
        
        # Reset
        cb.reset()
        
        # Verify reset
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0
    
    def test_reset_clears_failure_count(self):
        """Test reset clears failure count."""
        cb = CircuitBreaker("test")
        cb._failure_count = 5
        
        cb.reset()
        
        assert cb.failure_count == 0
    
    def test_reset_clears_last_failure_time(self):
        """Test reset clears last failure time."""
        cb = CircuitBreaker("test")
        cb._last_failure_time = 12345.0
        
        cb.reset()
        
        assert cb.last_failure_time is None


class TestCircuitBreakerLastFailureTime:
    """Test last failure time tracking."""
    
    def test_last_failure_time_set_on_failure(self):
        """Test that last_failure_time is set on failure."""
        cb = CircuitBreaker("test")
        
        assert cb.last_failure_time is None
        
        before = time.monotonic()
        cb._on_failure(Exception("Test error"))
        after = time.monotonic()
        
        assert cb.last_failure_time is not None
        assert before <= cb.last_failure_time <= after
    
    def test_last_failure_time_not_updated_on_success(self):
        """Test that last_failure_time is not updated on success."""
        cb = CircuitBreaker("test")
        
        cb._on_failure(Exception("Test error"))
        first_failure_time = cb.last_failure_time
        
        # Wait a bit
        time.sleep(0.01)
        
        # Success should not update last_failure_time
        cb._on_success()
        
        assert cb.last_failure_time == first_failure_time


class TestGetCircuitBreakerRegistry:
    """Test circuit breaker registry functionality."""
    
    def test_get_circuit_breaker_creates_new(self):
        """Test that get_circuit_breaker creates a new circuit breaker."""
        cb = get_circuit_breaker("new-breaker", failure_threshold=3)
        
        assert cb is not None
        assert cb.name == "new-breaker"
        assert cb.config.failure_threshold == 3
        assert cb.state == CircuitState.CLOSED
    
    def test_get_circuit_breaker_returns_existing(self):
        """Test that get_circuit_breaker returns existing circuit breaker."""
        cb1 = get_circuit_breaker("shared-breaker")
        cb2 = get_circuit_breaker("shared-breaker")
        
        assert cb1 is cb2
    
    def test_get_circuit_breaker_with_different_names(self):
        """Test that different names create different circuit breakers."""
        cb1 = get_circuit_breaker("breaker-1")
        cb2 = get_circuit_breaker("breaker-2")
        
        assert cb1 is not cb2


class TestRetryAndCircuitBreaker:
    """Test combined retry and circuit breaker decorator."""
    
    def test_combined_decorator_success(self):
        """Test combined decorator with successful function."""
        @with_retry_and_circuit_breaker(
            circuit_breaker_name="test-combined",
            max_attempts=3,
            failure_threshold=2,
        )
        def successful_func():
            return "success"
        
        result = successful_func()
        assert result == "success"
        
        cb = get_circuit_breaker("test-combined")
        assert cb.state == CircuitState.CLOSED
    
    def test_combined_decorator_failure_tracked(self):
        """Test combined decorator tracks failures."""
        call_count = 0
        
        @with_retry_and_circuit_breaker(
            circuit_breaker_name="test-combined-fail",
            max_attempts=2,
            failure_threshold=2,
        )
        def sometimes_fails():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Temporary error")
            return "success"
        
        result = sometimes_fails()
        assert result == "success"
        
        cb = get_circuit_breaker("test-combined-fail")
        # Should be CLOSED after successful recovery
        assert cb.state == CircuitState.CLOSED
