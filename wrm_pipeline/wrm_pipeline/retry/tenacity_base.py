"""Tenacity integration utilities for retry logic."""

import tenacity
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    before_sleep,
    retry_if_exception_type,
)
from typing import Callable, Type, Optional, Tuple
import logging

from .config import RetryConfiguration
from .exceptions import RetryExhaustedException

logger = logging.getLogger(__name__)


def get_wait_strategy(config: RetryConfiguration):
    """Create exponential jitter wait strategy from configuration.
    
    Args:
        config: RetryConfiguration with wait parameters
        
    Returns:
        Configured wait_exponential_jitter strategy
    """
    return wait_exponential_jitter(
        initial=config.base_delay,
        max=config.max_delay,
        exp_base=config.exponential_base,
        jitter=config.jitter,
    )


def get_stop_strategy(config: RetryConfiguration):
    """Create stop strategy from configuration.
    
    Args:
        config: RetryConfiguration with attempt limits
        
    Returns:
        Configured stop_after_attempt strategy
    """
    return stop_after_attempt(config.max_attempts)


def get_retry_strategy(
    config: Optional[RetryConfiguration],
    retry_if: Optional[Callable[[Exception], bool]] = None,
    default_exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Create retry strategy from configuration.
    
    Args:
        config: RetryConfiguration with exception types
        retry_if: Optional custom predicate for retry decisions
        default_exceptions: Default exception types to use if config is None
        
    Returns:
        Configured retry strategy (can handle multiple exception types)
    """
    # If retry_if is provided, use it directly (the caller is responsible for exception filtering)
    if retry_if is not None:
        return retry_if
    
    # Get exception types from config or use defaults
    exceptions = config.retry_on_exceptions if config else default_exceptions
    
    if len(exceptions) == 1:
        return retry_if_exception_type(exceptions[0])
    
    # Chain multiple exception types with | operator (tenacity 8+)
    retry_strategy = retry_if_exception_type(exceptions[0])
    for exc_type in exceptions[1:]:
        retry_strategy = retry_strategy | retry_if_exception_type(exc_type)
    
    return retry_strategy


def before_sleep_log(
    retry_state: tenacity.RetryCallState,
    logger: logging.Logger = logger,
) -> None:
    """Log before each retry attempt.
    
    Args:
        retry_state: Current retry state from tenacity
        logger: Logger instance to use
    """
    if retry_state.outcome is None:
        return
    
    exception = retry_state.outcome.exception()
    if exception:
        logger.warning(
            f"Retrying (attempt {retry_state.attempt_number}) "
            f"after exception: {type(exception).__name__}: {exception}"
        )


def get_tenacity_decorator(
    config: RetryConfiguration,
    retry_if: Optional[Callable[[tenacity.RetryCallState], bool]] = None,
) -> Callable:
    """Create a complete tenacity decorator from configuration.
    
    Args:
        config: Complete RetryConfiguration
        retry_if: Optional custom predicate for retry decisions
        
    Returns:
        Configured tenacity decorator
    """
    return retry(
        wait=get_wait_strategy(config),
        stop=get_stop_strategy(config),
        retry=get_retry_strategy(config, retry_if=retry_if),
        before_sleep=before_sleep_log,
    )
