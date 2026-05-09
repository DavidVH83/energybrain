"""Retry utility with exponential backoff for all HA API calls."""
import asyncio
import functools
from collections.abc import Callable
from typing import Any, TypeVar

from energybrain.exceptions import RetryExhaustedError
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_T = TypeVar("_T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_DELAY_S = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_DELAY_S = 30.0


async def retry_async(
    func: Callable[..., Any],
    *args: Any,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay_s: float = DEFAULT_INITIAL_DELAY_S,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_delay_s: float = DEFAULT_MAX_DELAY_S,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> Any:
    """Execute an async callable with exponential backoff retry.

    Args:
        func: Async callable to execute.
        *args: Positional arguments for func.
        max_attempts: Maximum number of total attempts (first try + retries).
        initial_delay_s: Delay in seconds before the second attempt.
        backoff_factor: Multiply delay by this factor after each failure.
        max_delay_s: Upper cap on inter-retry delay.
        retryable_exceptions: Exception types that trigger a retry.
        **kwargs: Keyword arguments for func.

    Returns:
        Return value of func on success.

    Raises:
        RetryExhaustedError: When all attempts are exhausted.
    """
    delay = initial_delay_s
    last_error: Exception = RuntimeError("No attempts made")

    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            logger.warning(
                "retry_attempt",
                attempt=attempt,
                max_attempts=max_attempts,
                delay_s=round(delay, 2),
                error=str(exc),
            )
            await asyncio.sleep(delay)
            delay = min(delay * backoff_factor, max_delay_s)

    raise RetryExhaustedError(max_attempts, last_error) from last_error


def with_retry(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay_s: float = DEFAULT_INITIAL_DELAY_S,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_delay_s: float = DEFAULT_MAX_DELAY_S,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    """Decorator that adds exponential backoff retry to an async function.

    Args:
        max_attempts: Maximum number of total attempts.
        initial_delay_s: Initial delay before the first retry.
        backoff_factor: Exponential backoff multiplier.
        max_delay_s: Maximum delay cap between retries.
        retryable_exceptions: Exception types that should trigger a retry.

    Returns:
        Decorator that wraps the target async function.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await retry_async(
                func,
                *args,
                max_attempts=max_attempts,
                initial_delay_s=initial_delay_s,
                backoff_factor=backoff_factor,
                max_delay_s=max_delay_s,
                retryable_exceptions=retryable_exceptions,
                **kwargs,
            )
        return wrapper
    return decorator
