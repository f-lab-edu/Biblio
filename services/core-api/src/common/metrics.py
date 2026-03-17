import threading
from collections import defaultdict
from typing import Dict, List


class _Registry:
    """In-process metrics registry (thread-safe, stdlib only).

    Exposes counters and a simple list-backed gauge for latency samples.
    This is intentionally minimal; integration/export is out of scope.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = defaultdict(int)
        self._latency_ms: Dict[str, List[float]] = defaultdict(list)

    # counters
    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    # latencies (milliseconds)
    def observe_ms(self, name: str, value_ms: float) -> None:
        with self._lock:
            self._latency_ms[name].append(float(value_ms))

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            # shallow copies to avoid external mutation
            return {
                "counters": dict(self._counters),
                "latencies_ms": {k: list(v) for k, v in self._latency_ms.items()},
            }


REGISTRY = _Registry()


# Convenience helpers for the spec-required metrics

def inc_mq_publish_fail() -> None:
    REGISTRY.inc("mq_publish_fail_count")


def inc_cursor_decode_fail() -> None:
    REGISTRY.inc("cursor_decode_fail_count")


def inc_complete_idempotent_hit() -> None:
    REGISTRY.inc("complete_idempotent_hit_count")


def observe_gcs_signed_url_latency_ms(value_ms: float) -> None:
    REGISTRY.observe_ms("gcs_signed_url_latency_ms", value_ms)

