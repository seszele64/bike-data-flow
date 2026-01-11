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


def get_retry_strategy(config: RetryConfiguration):
    """Create retry strategy from configuration.
    
    Args:
        config: RetryConfiguration with exception types
        
    Returns:
        Configured retry_if_exception_type strategy
    """
    return retry_if_exception_type(*config.retry_on_exceptions)


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


def get_tenacity_decorator(config: RetryConfiguration) -> Callable:
    """Create a complete tenacity decorator from configuration.
    
    Args:
        config: Complete RetryConfiguration
        
    Returns:
        Configured tenacity decorator
    """
    return retry(
        wait=get_wait_strategy(config),
        stop=get_stop_strategy(config),
        retry=get_retry_strategy(config),
        before_sleep=before_sleep_log,
    )
