from collections.abc import Awaitable, Callable


SleepCallable = Callable[[float], Awaitable[None]]
JitterCallable = Callable[[], float]

_JITTER_RATIO = 0.25


def exponential_backoff_with_jitter(attempt_index: int, jitter_value: float) -> float:
    return (2**attempt_index) * (1 + (jitter_value * _JITTER_RATIO))
