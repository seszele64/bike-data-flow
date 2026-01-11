"""Custom exceptions for the retry module."""


class RetryExhaustedException(Exception):
    """Raised when all retry attempts have been exhausted."""
    
    def __init__(self, message: str, attempts: int, last_exception: Exception):
        self.message = message
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(self.message)
    
    def __str__(self) -> str:
        return f"{self.message} (attempts: {self.attempts})"


class CircuitOpenException(Exception):
    """Raised when circuit breaker is open and operation is rejected."""
    
    def __init__(self, message: str, failure_count: int):
        self.message = message
        self.failure_count = failure_count
        super().__init__(self.message)
    
    def __str__(self) -> str:
        return f"{self.message} (failure_count: {self.failure_count})"
