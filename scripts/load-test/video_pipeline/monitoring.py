from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any

from infrastructure import CommandRunner, LoadTestError


_METRICS = {
    "run.googleapis.com/container/cpu/utilizations": "worker_cpu_percent",
    "run.googleapis.com/container/memory/utilizations": "worker_memory_percent",
    "run.googleapis.com/container/instance_count": "worker_instance_count",
}


def collect_cloud_run_monitoring_samples(
    commands: CommandRunner,
    *,
    project_id: str,
    service_name: str,
    start_time: str,
    end_time: str,
) -> tuple[dict[str, Any], ...]:
    access_token = commands.output(["gcloud", "auth", "print-access-token"])
    if not access_token:
        raise LoadTestError("Could not obtain an access token for Cloud Monitoring.")
    values_by_timestamp: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for metric_type, field_name in _METRICS.items():
        for series in _fetch_time_series(
            project_id=project_id,
            access_token=access_token,
            metric_type=metric_type,
            service_name=service_name,
            start_time=start_time,
            end_time=end_time,
        ):
            for point in series.get("points", []):
                parsed = _monitoring_point(point, field_name)
                if parsed is None:
                    continue
                timestamp, value = parsed
                values_by_timestamp[timestamp][field_name].append(value)
    return tuple(
        {
            "timestamp_utc": timestamp,
            "source": "cloud-monitoring",
            **{
                field_name: _aggregate_metric_values(field_name, values)
                for field_name, values in values_by_timestamp[timestamp].items()
            },
        }
        for timestamp in sorted(values_by_timestamp)
    )


def _fetch_time_series(
    *,
    project_id: str,
    access_token: str,
    metric_type: str,
    service_name: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    filter_expression = (
        f'metric.type = "{metric_type}" AND '
        'resource.type = "cloud_run_revision" AND '
        f'resource.labels.service_name = "{service_name}"'
    )
    base_url = f"https://monitoring.googleapis.com/v3/projects/{project_id}/timeSeries"
    series: list[dict[str, Any]] = []
    page_token = ""
    while True:
        parameters = {
            "filter": filter_expression,
            "interval.startTime": start_time,
            "interval.endTime": end_time,
            "view": "FULL",
        }
        if page_token:
            parameters["pageToken"] = page_token
        url = f"{base_url}?{urllib.parse.urlencode(parameters)}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LoadTestError(
                f"Could not collect Cloud Monitoring metric {metric_type}."
            ) from error
        raw_series = payload.get("timeSeries", [])
        if not isinstance(raw_series, list):
            raise LoadTestError("Cloud Monitoring timeSeries must be a list.")
        series.extend(item for item in raw_series if isinstance(item, dict))
        raw_page_token = payload.get("nextPageToken")
        if not raw_page_token:
            break
        page_token = str(raw_page_token)
    return series


def _monitoring_point(
    point: object,
    field_name: str,
) -> tuple[str, float] | None:
    if not isinstance(point, dict):
        return None
    interval = point.get("interval")
    value = point.get("value")
    if not isinstance(interval, dict) or not isinstance(value, dict):
        return None
    timestamp = interval.get("endTime")
    if not isinstance(timestamp, str):
        return None
    if field_name in {"worker_cpu_percent", "worker_memory_percent"}:
        distribution = value.get("distributionValue")
        if not isinstance(distribution, dict):
            return None
        mean = distribution.get("mean")
        if not isinstance(mean, (int, float)):
            return None
        return timestamp, float(mean) * 100.0
    raw_count = value.get("int64Value")
    try:
        return timestamp, float(raw_count)
    except (TypeError, ValueError):
        return None


def _aggregate_metric_values(field_name: str, values: list[float]) -> float:
    if field_name == "worker_instance_count":
        return sum(values)
    return sum(values) / len(values)
