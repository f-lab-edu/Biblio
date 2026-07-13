from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


class PollTimeoutError(TimeoutError):
    pass


def poll_until(
    *,
    name: str,
    check: Callable[[], T | None],
    timeout_seconds: float,
    interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> T:
    deadline = monotonic() + timeout_seconds
    last_value: T | None = None
    while monotonic() <= deadline:
        last_value = check()
        if last_value is not None:
            return last_value
        sleep(interval_seconds)
    raise PollTimeoutError(f"Timed out waiting for {name}. Last value: {last_value!r}")
