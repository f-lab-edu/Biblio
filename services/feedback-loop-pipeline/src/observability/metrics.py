from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


class MetricsRecorder(Protocol):
    def increment(
        self,
        name: str,
        *,
        value: int = 1,
        tags: Mapping[str, str] | None = None,
    ) -> None: ...


class NoopMetricsRecorder:
    def increment(
        self,
        name: str,
        *,
        value: int = 1,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        _ = name, value, tags


@dataclass(frozen=True)
class MetricEvent:
    name: str
    value: int
    tags: dict[str, str]


@dataclass
class InMemoryMetricsRecorder:
    events: list[MetricEvent] = field(default_factory=list)

    def increment(
        self,
        name: str,
        *,
        value: int = 1,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        self.events.append(MetricEvent(name=name, value=value, tags=dict(tags or {})))
