from collections.abc import Mapping
from typing import Protocol

from src.common.logging import info as log_info

MetricTags = Mapping[str, str]


class MetricsRecorder(Protocol):
    def increment_counter(
        self,
        name: str,
        tags: MetricTags | None = None,
    ) -> None: ...


class NoopMetricsRecorder:
    def increment_counter(
        self,
        name: str,
        tags: MetricTags | None = None,
    ) -> None:
        _ = name, tags
        return None


class LoggingMetricsRecorder:
    def increment_counter(
        self,
        name: str,
        tags: MetricTags | None = None,
    ) -> None:
        log_info(
            "metric_increment",
            metric_name=name,
            metric_tags=dict(tags or {}),
        )
