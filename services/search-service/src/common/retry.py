"""Common retry-with-backoff utility.

Usage: wrap retryable failures in RetryableError inside the attempt function.
Non-RetryableError exceptions propagate immediately without retry.
"""

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Raise inside an attempt function to signal the retry loop should continue.

    The ``cause`` is the actual exception to raise after retries are exhausted.
    """

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause))


async def retry_with_backoff(
    attempt_fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float = 0.2,
) -> T:
    """Execute *attempt_fn* up to ``max_retries + 1`` times with exponential backoff.

    * **RetryableError** → retry (if attempts remain) or raise ``exc.cause``
    * Any other exception → propagate immediately (no retry)
    """
    last_cause: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await attempt_fn()
        except RetryableError as exc:
            last_cause = exc.cause
            if attempt < max_retries:
                await asyncio.sleep(base_delay * (2**attempt))
    if last_cause is None:
        raise RuntimeError("retry loop exited without error")
    raise last_cause
