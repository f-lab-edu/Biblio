#!/usr/bin/env python3
"""영상 파이프라인 S3·S4 구버전/리팩토링 비교 재집계 스크립트.

네 개의 완주한 load-test 산출물을 읽어 처리량, 완료 지연, 초기 Queue 대기,
stage 시간, Queue 적체, 자원 사용, 신뢰성 지표를 다시 계산한다.

원본 파일은 읽기만 하고 수정하지 않는다. 표준 라이브러리만 사용한다.

percentile 정의
---------------
정렬한 표본에 대해 선형 보간을 쓴다 (numpy 기본 'linear', R type 7과 동일).
    rank = (n - 1) * p
    lo = floor(rank), hi = ceil(rank)
    value = x[lo] + (x[hi] - x[lo]) * (rank - lo)
표본이 1개면 그 값을, 0개면 null을 돌려준다.

누락값 처리
-----------
값이 없으면 0으로 바꾸지 않고 `missing` 카운트에 넣는다. 집계 통계의 분모는
항상 실제로 값이 있는 표본 수(`count`)다.

사용법
------
    python3 scripts/load-test/analyze_video_pipeline_comparison.py \
        [--artifact-root artifacts/load-tests/video-pipeline] \
        [--out "docs/테스트결과 분석/video-pipeline-s3-s4-metrics.json"]
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import statistics
import tarfile
import datetime as dt
from typing import Any, Iterable

FORMULA_VERSION = "video-pipeline-s3-s4-comparison/1.0.0"

DEFAULT_ARTIFACT_ROOT = "artifacts/load-tests/video-pipeline"

# 보고서와 같은 폴더에 둔다. `artifacts/load-tests/`는 .gitignore 대상이라
# 그쪽에 두면 버전 관리에서 빠진다.
DEFAULT_METRICS_OUT = "docs/테스트결과 분석/video-pipeline-s3-s4-metrics.json"

# 비교 대상 실행 4개. 키는 보고서에서 쓰는 이름이다.
RUN_DIRS: dict[str, str] = {
    "s3_legacy": "20260816T122033Z-video-s3",
    "s3_refactored": (
        "20260825T183422Z-video-s3-refactored-norm2-embed2-maxlen1024-wait300-worker450"
    ),
    "s4_legacy": (
        "20260825T1812KST-video-s4-baseline-f572829-sttfix-ffmpegfix-gcs300-maxlen1024"
    ),
    "s4_refactored": (
        "20260826T005351Z-video-s4-refactored-assembly-retry-norm2-embed2"
        "-maxlen1024-wait300-worker450"
    ),
}

# 보조 근거로만 쓰는 중간 설정 실행. 주 비교 통계에는 넣지 않는다.
SUPPLEMENTARY_RUN_DIRS: dict[str, str] = {
    "s3_refactored_embed1": (
        "20260825T150803Z-video-s3-refactored-maxlen1024-wait300-worker450"
    ),
}

# 구버전 S3의 EMBEDDING_MAX_LENGTH를 교차 확인할 때만 쓰는 제외 자료.
# 완주 성능 통계에는 절대 넣지 않는다.
MAXLEN_REFERENCE_RUN = (
    "comparison-excluded/20260817T033406Z-video-s4-maxlen256"
)

LEGACY_STAGES = ["download", "audio", "stt", "chunk_enrichment", "embedding", "persist"]
REFACTORED_STAGES = ["NORMALIZE_VIDEO", "TRANSCRIBE_PART", "ENRICH_CHUNK", "EMBED_BATCH"]

# 회차(wave) 경계 판정. 같은 회차의 업로드 시작은 수 초 안에 몰리고,
# 회차 사이에는 최소 수백 초 공백이 있다.
ROUND_GAP_SECONDS = 120.0

CPU_THRESHOLDS = (25.0, 50.0, 75.0)
IDLE_CPU_THRESHOLD = 5.0


# --------------------------------------------------------------------------- #
# 기본 유틸
# --------------------------------------------------------------------------- #
def parse_ts(value: Any) -> dt.datetime | None:
    """ISO 8601 문자열을 timezone-aware UTC datetime으로 바꾼다."""
    if value is None or value == "" or value == "-":
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    return None if value is None else value.astimezone(dt.timezone.utc).isoformat()


def to_float(value: Any) -> float | None:
    """숫자 또는 숫자 문자열을 float으로 바꾼다. 실패하면 None(누락)."""
    if value is None or value == "" or value == "-":
        return None
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def percentile(sorted_values: list[float], fraction: float) -> float | None:
    """선형 보간 percentile (numpy 'linear' / R type 7)."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * fraction
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (rank - low)


def describe(raw_values: Iterable[Any], missing: int = 0) -> dict[str, Any]:
    """count/mean/median/p90/p95/max 등 기본 분포 요약."""
    values = [v for v in (to_float(x) for x in raw_values) if v is not None]
    extra_missing = missing
    ordered = sorted(values)
    summary: dict[str, Any] = {
        "count": len(ordered),
        "missing": extra_missing,
        "min": ordered[0] if ordered else None,
        "mean": statistics.fmean(ordered) if ordered else None,
        "median": percentile(ordered, 0.5),
        "p90": percentile(ordered, 0.90),
        "p95": percentile(ordered, 0.95),
        "max": ordered[-1] if ordered else None,
        "sum": sum(ordered) if ordered else None,
    }
    if len(ordered) >= 2:
        stdev = statistics.stdev(ordered)
        summary["stdev"] = stdev
        summary["cv"] = (stdev / summary["mean"]) if summary["mean"] else None
    else:
        summary["stdev"] = None
        summary["cv"] = None
    return summary


def fraction_at_or_above(values: list[float], threshold: float) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v >= threshold) / len(values)


def time_weighted_mean(points: list[tuple[dt.datetime, float]]) -> float | None:
    """표본 간격이 불균일할 때 쓰는 시간 가중 평균.

    각 표본은 다음 표본까지의 구간을 대표한다고 본다. 마지막 표본은
    직전 구간 길이를 그대로 쓴다.
    """
    if not points:
        return None
    if len(points) == 1:
        return points[0][1]
    ordered = sorted(points, key=lambda p: p[0])
    total_weight = 0.0
    total = 0.0
    previous_gap = None
    for index in range(len(ordered)):
        if index < len(ordered) - 1:
            gap = (ordered[index + 1][0] - ordered[index][0]).total_seconds()
            previous_gap = gap
        else:
            gap = previous_gap if previous_gap is not None else 0.0
        if gap <= 0:
            continue
        total += ordered[index][1] * gap
        total_weight += gap
    if total_weight <= 0:
        return statistics.fmean(v for _, v in ordered)
    return total / total_weight


def ratio_change(new: float | None, old: float | None) -> float | None:
    """(new - old) / old. 음수면 감소."""
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old


def max_overlap(intervals: list[tuple[dt.datetime, dt.datetime]]) -> int:
    """구간 목록의 최대 동시 실행 개수."""
    marks: list[tuple[dt.datetime, int]] = []
    for start, end in intervals:
        if start is None or end is None:
            continue
        marks.append((start, 1))
        marks.append((end, -1))
    marks.sort(key=lambda m: (m[0], -m[1]))
    running = 0
    peak = 0
    for _, delta in marks:
        running += delta
        peak = max(peak, running)
    return peak


def read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# --------------------------------------------------------------------------- #
# 실행 로딩
# --------------------------------------------------------------------------- #
class Run:
    """하나의 load-test 실행 산출물."""

    def __init__(self, key: str, path: str) -> None:
        self.key = key
        self.path = path
        self.environment = json.load(open(os.path.join(path, "environment.json"), encoding="utf-8"))
        self.fixtures = json.load(open(os.path.join(path, "fixtures.json"), encoding="utf-8"))
        self.videos = read_jsonl(os.path.join(path, "video-results.jsonl"))
        self.events = read_jsonl(os.path.join(path, "events.jsonl"))
        self.samples = read_jsonl(os.path.join(path, "samples.jsonl"))
        self.schema = "refactored" if self._is_refactored() else "legacy"
        self._prepare_videos()

    def _is_refactored(self) -> bool:
        for event in self.events:
            if event.get("event_type") == "pipeline.work.started":
                return True
        return False

    # -- 영상 단위 파생값 ---------------------------------------------------- #
    def _prepare_videos(self) -> None:
        for video in self.videos:
            video["_started"] = parse_ts(video.get("complete_started_at"))
            video["_responded"] = parse_ts(video.get("complete_responded_at"))
            video["_terminal"] = parse_ts(video.get("terminal_observed_at"))
            video["_duration_seconds"] = (
                self.fixtures.get(video["fixture"], {}).get("duration_seconds")
            )
        self.videos.sort(key=lambda v: (v["_started"] or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))
        self._assign_rounds()
        self._attach_first_work_start()

    def _assign_rounds(self) -> None:
        round_index = 0
        previous: dt.datetime | None = None
        for video in self.videos:
            started = video["_started"]
            if previous is not None and started is not None:
                if (started - previous).total_seconds() > ROUND_GAP_SECONDS:
                    round_index += 1
            video["_round"] = round_index
            if started is not None:
                previous = started

    def _attach_first_work_start(self) -> None:
        """영상별 '첫 실제 Worker 작업 시작' 시각.

        구버전: pipeline.stage.started(stage=download)
        리팩토링: pipeline.work.started(stage=NORMALIZE_VIDEO)
        두 값 모두 업로드 완료 응답 이후 Worker가 실제로 그 영상을 집어든 시점이다.
        """
        first: dict[str, dt.datetime] = {}
        for event in self.events:
            video_id = event.get("video_id")
            if not video_id or video_id == "-":
                continue
            event_type = event.get("event_type")
            if self.schema == "legacy":
                if event_type != "pipeline.stage.started" or event.get("stage") != "download":
                    continue
            else:
                if event_type != "pipeline.work.started" or event.get("stage") != "NORMALIZE_VIDEO":
                    continue
            timestamp = parse_ts(event.get("timestamp_utc"))
            if timestamp is None:
                continue
            if video_id not in first or timestamp < first[video_id]:
                first[video_id] = timestamp
        for video in self.videos:
            video["_first_work_start"] = first.get(video["video_id"])

    # -- 편의 접근자 --------------------------------------------------------- #
    def events_of(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("event_type") == event_type]

    def samples_of(self, source: str) -> list[dict[str, Any]]:
        return [s for s in self.samples if s.get("source") == source]

    @property
    def trace_to_video(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for video in self.videos:
            trace = video.get("trace_id")
            if trace:
                mapping[trace] = video["video_id"]
        return mapping

    @property
    def rounds(self) -> list[int]:
        return sorted({v["_round"] for v in self.videos})

    @property
    def active_window(self) -> tuple[dt.datetime | None, dt.datetime | None]:
        starts = [v["_started"] for v in self.videos if v["_started"]]
        ends = [v["_terminal"] for v in self.videos if v["_terminal"]]
        return (min(starts) if starts else None, max(ends) if ends else None)


# --------------------------------------------------------------------------- #
# 계약 / 데이터 품질
# --------------------------------------------------------------------------- #
def contract_block(run: Run) -> dict[str, Any]:
    environment = run.environment
    runtime = environment.get("runtime_config") or {}
    worker = runtime.get("pipeline_worker") or {}
    embedding_vm = runtime.get("embedding_vm") or {}
    started = parse_ts(environment.get("started_at"))
    finished = parse_ts(environment.get("finished_at"))
    window_start, window_end = run.active_window

    fixture_used = collections.Counter(v["fixture"] for v in run.videos)
    fixture_detail = {
        name: {
            "sha256": spec.get("sha256"),
            "duration_seconds": spec.get("duration_seconds"),
            "size_bytes": spec.get("size_bytes"),
            "requests": fixture_used.get(name, 0),
        }
        for name, spec in run.fixtures.items()
    }

    terminal_counts = collections.Counter(v.get("terminal_status") for v in run.videos)
    request_errors = sum(1 for v in run.videos if v.get("request_error"))
    missing_terminal = sum(1 for v in run.videos if v["_terminal"] is None)

    return {
        "run_id": environment.get("run_id"),
        "artifact_path": run.path,
        "preset": (environment.get("plan") or {}).get("preset"),
        "repeat_count": (environment.get("plan") or {}).get("repeat_count"),
        "phases": (environment.get("plan") or {}).get("phases"),
        "timing_schema": run.schema,
        "artifact_schema_version": environment.get("artifact_schema_version"),
        "status": environment.get("status"),
        "workload_status": environment.get("workload_status"),
        "observability_status": environment.get("observability_status"),
        "observability_errors": environment.get("observability_errors"),
        "git_sha": environment.get("git_sha"),
        "container_image": worker.get("container_image"),
        "worker_revision": worker.get("latest_ready_revision"),
        "worker_resources": worker.get("resources"),
        "worker_container_concurrency": worker.get("container_concurrency"),
        "worker_max_scale": (worker.get("annotations") or {}).get(
            "autoscaling.knative.dev/maxScale"
        ),
        "worker_min_scale": (worker.get("annotations") or {}).get(
            "autoscaling.knative.dev/minScale"
        ),
        "worker_environment": worker.get("environment"),
        "embedding_vm_config": embedding_vm.get("config"),
        "runtime_config_availability": runtime.get("availability"),
        "runtime_config_recorded": bool(worker or embedding_vm),
        "recovery_context": environment.get("recovery_context"),
        "runner_started_at": iso(started),
        "runner_finished_at": iso(finished),
        "runner_wall_clock_seconds": (
            (finished - started).total_seconds() if started and finished else None
        ),
        "workload_active_window_start": iso(window_start),
        "workload_active_window_end": iso(window_end),
        "workload_active_window_seconds": (
            (window_end - window_start).total_seconds() if window_start and window_end else None
        ),
        "total_requests": (environment.get("workload") or {}).get("total_requests"),
        "request_counts": (environment.get("workload") or {}).get("request_counts"),
        "total_fixture_duration_seconds": (environment.get("workload") or {}).get(
            "total_fixture_duration_seconds"
        ),
        "fixtures": fixture_detail,
        "fixtures_manifest_sha256_by_name": {
            name: spec.get("sha256") for name, spec in run.fixtures.items()
        },
        "collection": environment.get("collection"),
        "event_count_observed": len(run.events),
        "sample_count_observed": len(run.samples),
        "video_result_rows": len(run.videos),
        "terminal_status_counts": dict(terminal_counts),
        "request_error_count": request_errors,
        "missing_terminal_observation": missing_terminal,
        "event_type_counts": dict(
            collections.Counter(e.get("event_type") for e in run.events)
        ),
        "sample_source_counts": dict(
            collections.Counter(s.get("source") for s in run.samples)
        ),
        "target_vm_summary": environment.get("target_vm_summary"),
    }


# --------------------------------------------------------------------------- #
# 완료 지연 / 공정성
# --------------------------------------------------------------------------- #
def build_video_latency_rows(run: Run) -> list[dict[str, Any]]:
    """영상별 완료 지연·초기 Queue 대기·slowdown ratio."""
    rows: list[dict[str, Any]] = []
    for video in run.videos:
        responded = video["_responded"]
        started = video["_started"]
        terminal = video["_terminal"]
        first_work = video["_first_work_start"]
        duration = video["_duration_seconds"]

        completion_latency = (
            (terminal - responded).total_seconds() if responded and terminal else None
        )
        rows.append(
            {
                "video_id": video["video_id"],
                "fixture": video["fixture"],
                "round": video["_round"],
                "trace_id": video.get("trace_id"),
                "terminal_status": video.get("terminal_status"),
                "complete_started_at": iso(started),
                "complete_responded_at": iso(responded),
                "first_work_started_at": iso(first_work),
                "terminal_observed_at": iso(terminal),
                "upload_request_seconds": (
                    (responded - started).total_seconds()
                    if started and responded
                    else None
                ),
                "completion_latency_seconds": completion_latency,
                "request_to_terminal_seconds": (
                    (terminal - started).total_seconds() if started and terminal else None
                ),
                "initial_queue_wait_seconds": (
                    (first_work - responded).total_seconds()
                    if responded and first_work
                    else None
                ),
                "fixture_duration_seconds": duration,
                "slowdown_ratio": (
                    completion_latency / duration
                    if completion_latency is not None and duration
                    else None
                ),
                "pipeline_timing": video.get("pipeline_timing"),
            }
        )
    return rows


def group(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """행 목록에서 한 필드의 분포. None은 missing으로 센다."""
    values = [r[field] for r in rows]
    return describe(values, missing=sum(1 for v in values if v is None))


def latency_by_round(run: Run, per_video: list[dict[str, Any]]) -> dict[str, Any]:
    """회차별 makespan·첫 완료·완료 간격·완료 순서."""
    by_round: dict[str, Any] = {}
    for round_index in run.rounds:
        rows = [r for r in per_video if r["round"] == round_index]
        starts = [s for s in (parse_ts(r["complete_started_at"]) for r in rows) if s]
        ends = [e for e in (parse_ts(r["terminal_observed_at"]) for r in rows) if e]
        by_round[str(round_index)] = {
            "video_count": len(rows),
            "first_request_at": iso(min(starts)) if starts else None,
            "last_terminal_at": iso(max(ends)) if ends else None,
            "makespan_seconds": (
                (max(ends) - min(starts)).total_seconds() if starts and ends else None
            ),
            "first_completion_seconds": (
                (min(ends) - min(starts)).total_seconds() if starts and ends else None
            ),
            "completion_spread_seconds": (
                (max(ends) - min(ends)).total_seconds() if len(ends) >= 2 else None
            ),
            "completion_latency_seconds": group(rows, "completion_latency_seconds"),
            "initial_queue_wait_seconds": group(rows, "initial_queue_wait_seconds"),
            "completion_order": [
                {
                    "fixture": r["fixture"],
                    "video_id": r["video_id"],
                    "terminal_observed_at": r["terminal_observed_at"],
                    "completion_latency_seconds": r["completion_latency_seconds"],
                }
                for r in rounds_sorted(rows)
            ],
        }
    return by_round


def latency_block(run: Run) -> dict[str, Any]:
    per_video = build_video_latency_rows(run)

    by_fixture: dict[str, Any] = {}
    for fixture in sorted({r["fixture"] for r in per_video}):
        rows = [r for r in per_video if r["fixture"] == fixture]
        by_fixture[fixture] = {
            "video_count": len(rows),
            "completion_latency_seconds": group(rows, "completion_latency_seconds"),
            "request_to_terminal_seconds": group(rows, "request_to_terminal_seconds"),
            "initial_queue_wait_seconds": group(rows, "initial_queue_wait_seconds"),
            "slowdown_ratio": group(rows, "slowdown_ratio"),
            "worst_video": max(
                (r for r in rows if r["completion_latency_seconds"] is not None),
                key=lambda r: r["completion_latency_seconds"],
                default=None,
            ),
        }

    by_round = latency_by_round(run, per_video)

    makespans = [
        by_round[str(i)]["makespan_seconds"]
        for i in run.rounds
        if by_round[str(i)]["makespan_seconds"] is not None
    ]

    return {
        "per_video": per_video,
        "overall": {
            "completion_latency_seconds": group(per_video, "completion_latency_seconds"),
            "request_to_terminal_seconds": group(per_video, "request_to_terminal_seconds"),
            "initial_queue_wait_seconds": group(per_video, "initial_queue_wait_seconds"),
            "slowdown_ratio": group(per_video, "slowdown_ratio"),
            "upload_request_seconds": group(per_video, "upload_request_seconds"),
        },
        "by_fixture": by_fixture,
        "by_round": by_round,
        "round_makespan_seconds": describe(makespans),
    }


def fairness_block(run: Run, latency: dict[str, Any]) -> dict[str, Any] | None:
    """S4 전용: long/short 완료 순서와 head-of-line blocking 판정."""
    fixtures = {r["fixture"] for r in latency["per_video"]}
    if not {"long", "short"} <= fixtures:
        return None

    rounds: dict[str, Any] = {}
    short_before_first_long = 0
    short_before_last_long = 0
    short_total = 0
    for round_index in run.rounds:
        rows = [r for r in latency["per_video"] if r["round"] == round_index]
        longs = [
            r for r in rows if r["fixture"] == "long" and r["terminal_observed_at"]
        ]
        shorts = [
            r for r in rows if r["fixture"] == "short" and r["terminal_observed_at"]
        ]
        if not longs or not shorts:
            continue
        first_long = min(r["terminal_observed_at"] for r in longs)
        last_long = max(r["terminal_observed_at"] for r in longs)
        before_first = sum(1 for r in shorts if r["terminal_observed_at"] < first_long)
        before_last = sum(1 for r in shorts if r["terminal_observed_at"] < last_long)
        short_before_first_long += before_first
        short_before_last_long += before_last
        short_total += len(shorts)

        # long이 실행 중일 때 short가 완료됐는가:
        # long의 첫 작업 시작 ~ long terminal 사이에 short terminal이 들어오는지 본다.
        long_active_windows = [
            (
                parse_ts(r["first_work_started_at"]),
                parse_ts(r["terminal_observed_at"]),
            )
            for r in longs
            if r["first_work_started_at"] and r["terminal_observed_at"]
        ]
        completed_while_long_running = 0
        for short in shorts:
            terminal = parse_ts(short["terminal_observed_at"])
            if terminal is None:
                continue
            if any(start <= terminal <= end for start, end in long_active_windows):
                completed_while_long_running += 1

        rounds[str(round_index)] = {
            "long_count": len(longs),
            "short_count": len(shorts),
            "first_long_terminal_at": first_long,
            "last_long_terminal_at": last_long,
            "short_completed_before_first_long": before_first,
            "short_completed_before_last_long": before_last,
            "short_completed_while_a_long_was_running": completed_while_long_running,
            "terminal_order": [
                r["fixture"] for r in rounds_sorted(rows)
            ],
        }

    return {
        "by_round": rounds,
        "short_total": short_total,
        "short_completed_before_first_long_total": short_before_first_long,
        "short_completed_before_last_long_total": short_before_last_long,
    }


def rounds_sorted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (r for r in rows if r["terminal_observed_at"]),
        key=lambda r: r["terminal_observed_at"],
    )


# --------------------------------------------------------------------------- #
# 작업량 / 처리량
# --------------------------------------------------------------------------- #
def workload_block(run: Run) -> dict[str, Any]:
    """실제 생성된 chunk 수, embedding text 수, embedding request 수."""
    chunks_by_video: collections.Counter[str] = collections.Counter()
    chunk_source = None

    if run.schema == "refactored":
        chunk_source = "assembly.succeeded.chunks_generated"
        for event in run.events_of("assembly.succeeded"):
            video_id = event.get("video_id")
            if video_id and video_id != "-":
                chunks_by_video[video_id] += int(event.get("chunks_generated") or 0)
    else:
        # 구버전 S4는 enrichment.step(step=vision)이 chunk마다 한 번 나온다.
        vision_steps = [
            e for e in run.events_of("enrichment.step") if e.get("step") == "vision"
        ]
        if vision_steps:
            chunk_source = "enrichment.step(step=vision) 건수"
            for event in vision_steps:
                video_id = event.get("video_id")
                if video_id and video_id != "-":
                    chunks_by_video[video_id] += 1

    # embedding text 수: 요청 단위 text_count 합계.
    embedding_requests = run.events_of("embedding.request.success")
    embedding_retries = run.events_of("embedding.request.retry")
    embedding_texts = sum(int(e.get("text_count") or 0) for e in embedding_requests)
    batch_sizes = [int(e.get("text_count") or 0) for e in embedding_requests]

    # 영상별 embedding text 귀속.
    texts_by_video: collections.Counter[str] = collections.Counter()
    attribution = None
    if run.schema == "refactored":
        attribution = "embedding.batch.completed.participant_video_ids 비례 배분"
        for event in run.events_of("embedding.batch.completed"):
            participants = event.get("participant_video_ids") or []
            size = int(event.get("batch_size") or 0)
            if not participants or size <= 0:
                continue
            share = size / len(participants)
            for video_id in participants:
                texts_by_video[video_id] += share
    else:
        attribution = "embedding.request.success.trace_id → video-results.trace_id 매핑"
        mapping = run.trace_to_video
        for event in embedding_requests:
            video_id = mapping.get(event.get("trace_id"))
            if video_id:
                texts_by_video[video_id] += int(event.get("text_count") or 0)

    per_video = []
    for video in run.videos:
        video_id = video["video_id"]
        per_video.append(
            {
                "video_id": video_id,
                "fixture": video["fixture"],
                "round": video["_round"],
                "chunks": chunks_by_video.get(video_id),
                "embedding_texts": texts_by_video.get(video_id),
            }
        )

    return {
        "chunk_source": chunk_source,
        "chunk_count_available": bool(chunks_by_video),
        "chunk_count_unavailable_reason": (
            None
            if chunks_by_video
            else "이 실행의 events.jsonl에는 chunk 단위 이벤트가 없다. "
            "embedding text 수(enriched chunk 1건당 1 text)를 작업량 대리 지표로 쓴다."
        ),
        "chunks_total": sum(chunks_by_video.values()) if chunks_by_video else None,
        "chunks_per_video": describe(
            [chunks_by_video[v["video_id"]] for v in run.videos if v["video_id"] in chunks_by_video],
            missing=sum(1 for v in run.videos if v["video_id"] not in chunks_by_video),
        ),
        "chunks_by_fixture": {
            fixture: describe(
                [
                    chunks_by_video[v["video_id"]]
                    for v in run.videos
                    if v["fixture"] == fixture and v["video_id"] in chunks_by_video
                ]
            )
            for fixture in sorted({v["fixture"] for v in run.videos})
        },
        "embedding_text_attribution": attribution,
        "embedding_requests_success": len(embedding_requests),
        "embedding_requests_retry": len(embedding_retries),
        "embedding_texts_total": embedding_texts,
        "embedding_batch_size_distribution": dict(
            sorted(collections.Counter(batch_sizes).items())
        ),
        "embedding_batch_size": describe(batch_sizes),
        "per_video": per_video,
    }


def throughput_block(run: Run, latency: dict[str, Any], workload: dict[str, Any]) -> dict[str, Any]:
    contract = run.environment
    started = parse_ts(contract.get("started_at"))
    finished = parse_ts(contract.get("finished_at"))
    window_start, window_end = run.active_window

    runner_wall = (finished - started).total_seconds() if started and finished else None
    active_window = (
        (window_end - window_start).total_seconds() if window_start and window_end else None
    )
    makespan_sum = sum(
        latency["by_round"][str(i)]["makespan_seconds"]
        for i in run.rounds
        if latency["by_round"][str(i)]["makespan_seconds"] is not None
    )

    completed = sum(1 for v in run.videos if v.get("terminal_status") == "READY")
    media_seconds = sum(
        (v["_duration_seconds"] or 0.0)
        for v in run.videos
        if v.get("terminal_status") == "READY"
    )
    chunks = workload["chunks_total"]
    texts = workload["embedding_texts_total"]
    requests = workload["embedding_requests_success"]

    def rates(basis_seconds: float | None) -> dict[str, Any]:
        if not basis_seconds or basis_seconds <= 0:
            return {
                "basis_seconds": basis_seconds,
                "videos_per_hour": None,
                "media_hours_per_hour": None,
                "chunks_per_hour": None,
                "embedding_texts_per_hour": None,
                "embedding_requests_per_hour": None,
            }
        hours = basis_seconds / 3600.0
        return {
            "basis_seconds": basis_seconds,
            "videos_per_hour": completed / hours,
            "media_hours_per_hour": (media_seconds / 3600.0) / hours,
            "chunks_per_hour": (chunks / hours) if chunks is not None else None,
            "embedding_texts_per_hour": (texts / hours) if texts is not None else None,
            "embedding_requests_per_hour": (requests / hours) if requests is not None else None,
        }

    per_round = {}
    for round_index in run.rounds:
        block = latency["by_round"][str(round_index)]
        makespan = block["makespan_seconds"]
        rows = [r for r in latency["per_video"] if r["round"] == round_index]
        round_completed = sum(1 for r in rows if r["terminal_status"] == "READY")
        round_media = sum(
            (r["fixture_duration_seconds"] or 0.0)
            for r in rows
            if r["terminal_status"] == "READY"
        )
        per_round[str(round_index)] = {
            "completed_videos": round_completed,
            "makespan_seconds": makespan,
            "videos_per_hour": (
                round_completed / (makespan / 3600.0) if makespan else None
            ),
            "media_hours_per_hour": (
                (round_media / 3600.0) / (makespan / 3600.0) if makespan else None
            ),
        }

    round_rates = [
        v["videos_per_hour"] for v in per_round.values() if v["videos_per_hour"] is not None
    ]

    return {
        "completed_videos": completed,
        "completed_media_seconds": media_seconds,
        "chunks_total": chunks,
        "embedding_texts_total": texts,
        "embedding_requests_total": requests,
        "runner_wall_clock": rates(runner_wall),
        "workload_active_window": rates(active_window),
        "round_makespan_sum": rates(makespan_sum if makespan_sum else None),
        "per_round": per_round,
        "round_videos_per_hour": describe(round_rates),
        "observability_tail_seconds": (
            (finished - window_end).total_seconds() if finished and window_end else None
        ),
        "inter_round_idle_seconds": (
            (active_window - makespan_sum)
            if active_window is not None and makespan_sum
            else None
        ),
    }


# --------------------------------------------------------------------------- #
# stage 분석
# --------------------------------------------------------------------------- #
def stage_block(run: Run) -> dict[str, Any]:
    if run.schema == "legacy":
        return legacy_stage_block(run)
    return refactored_stage_block(run)


def collect_legacy_stage_intervals(
    run: Run,
) -> tuple[list[dict[str, Any]], int, dict[tuple[str, str], dt.datetime]]:
    """pipeline.stage.started/finished 쌍을 구간으로 묶는다.

    반환값은 (구간 목록, 짝 없는 finished 수, 아직 안 끝난 started)다.
    """
    open_stages: dict[tuple[str, str], dt.datetime] = {}
    intervals: list[dict[str, Any]] = []
    unmatched_finish = 0
    for event in sorted(run.events, key=lambda e: e.get("timestamp_utc") or ""):
        event_type = event.get("event_type")
        if event_type not in ("pipeline.stage.started", "pipeline.stage.finished"):
            continue
        video_id = event.get("video_id")
        stage = event.get("stage")
        timestamp = parse_ts(event.get("timestamp_utc"))
        if not video_id or not stage or timestamp is None:
            continue
        key = (video_id, stage)
        if event_type == "pipeline.stage.started":
            open_stages[key] = timestamp
            continue
        start = open_stages.pop(key, None)
        if start is None:
            unmatched_finish += 1
            continue
        intervals.append(
            {
                "video_id": video_id,
                "stage": stage,
                "start": start,
                "end": timestamp,
                "seconds": (timestamp - start).total_seconds(),
                "status": event.get("status"),
            }
        )
    return intervals, unmatched_finish, open_stages


def summarize_legacy_stages(
    intervals: list[dict[str, Any]], fixture_by_video: dict[str, str]
) -> dict[str, Any]:
    """구버전 stage별 실행시간 분포와 최대 동시 실행 수."""
    stages: dict[str, Any] = {}
    for stage in LEGACY_STAGES:
        rows = [i for i in intervals if i["stage"] == stage]
        by_fixture = {}
        for fixture in sorted(
            {fixture_by_video[r["video_id"]] for r in rows if r["video_id"] in fixture_by_video}
        ):
            by_fixture[fixture] = describe(
                [
                    r["seconds"]
                    for r in rows
                    if fixture_by_video.get(r["video_id"]) == fixture
                ]
            )
        stages[stage] = {
            "measurement": (
                "pipeline.stage.started → pipeline.stage.finished (영상 단위, 순차)"
            ),
            "executions": len(rows),
            "execution_seconds": describe([r["seconds"] for r in rows]),
            "by_fixture": by_fixture,
            "max_concurrent_executions": max_overlap(
                [(r["start"], r["end"]) for r in rows]
            ),
        }
    return stages


def summarize_legacy_enrichment_steps(
    run: Run, fixture_by_video: dict[str, str]
) -> dict[str, Any]:
    """구버전 S4의 `enrichment.step` chunk 단위 세부 작업.

    리팩토링의 프레임 추출·Vision과 총량을 대조할 수 있는 유일한 근거다.
    구버전 S3에는 이 이벤트가 없어 빈 dict를 돌려준다.
    """
    step_events = run.events_of("enrichment.step")
    if not step_events:
        return {}
    per_step_video: dict[str, dict[str, float]] = collections.defaultdict(
        lambda: collections.defaultdict(float)
    )
    per_step_count: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: collections.defaultdict(int)
    )
    per_step_each: dict[str, list[float]] = collections.defaultdict(list)
    for event in step_events:
        step = event.get("step")
        video_id = event.get("video_id")
        if not step or not video_id or video_id == "-":
            continue
        seconds = (to_float(event.get("duration_ms")) or 0.0) / 1000.0
        per_step_video[step][video_id] += seconds
        per_step_count[step][video_id] += 1
        per_step_each[step].append(seconds)

    result: dict[str, Any] = {}
    for step in sorted(per_step_video):
        by_fixture = {}
        for fixture in sorted(
            {fixture_by_video.get(v) for v in per_step_video[step]} - {None}
        ):
            by_fixture[fixture] = {
                "per_video_total_seconds": describe(
                    [
                        total
                        for video_id, total in per_step_video[step].items()
                        if fixture_by_video.get(video_id) == fixture
                    ]
                ),
                "executions_per_video": describe(
                    [
                        count
                        for video_id, count in per_step_count[step].items()
                        if fixture_by_video.get(video_id) == fixture
                    ]
                ),
            }
        result[step] = {
            "executions": len(per_step_each[step]),
            "per_execution_seconds": describe(per_step_each[step]),
            "by_fixture": by_fixture,
        }
    return result


def legacy_stage_block(run: Run) -> dict[str, Any]:
    """pipeline.stage.started/finished 쌍으로 stage 실행시간을 만든다.

    구버전 stage는 한 영상 안에서 순차 실행이므로 stage 시간의 합이
    영상 처리시간에 가깝다.
    """
    intervals, unmatched_finish, open_stages = collect_legacy_stage_intervals(run)
    fixture_by_video = {v["video_id"]: v["fixture"] for v in run.videos}
    stages = summarize_legacy_stages(intervals, fixture_by_video)
    enrichment_steps = summarize_legacy_enrichment_steps(run, fixture_by_video)

    return {
        "schema": "legacy",
        "enrichment_steps": enrichment_steps,
        "unmatched_stage_finish": unmatched_finish,
        "open_stage_without_finish": len(open_stages),
        "stages": stages,
        "max_concurrent_videos": max_overlap(
            [
                (
                    min(i["start"] for i in intervals if i["video_id"] == video_id),
                    max(i["end"] for i in intervals if i["video_id"] == video_id),
                )
                for video_id in {i["video_id"] for i in intervals}
            ]
        ),
    }


def stage_running_limits(run: Run) -> dict[str, float]:
    """`pipeline-db` 표본에서 관측된 stage별 최대 동시 점유 수.

    이벤트 구간 겹침으로 세면 한 작업의 종료와 다음 작업의 시작이
    수백 마이크로초 겹치는 경계에서 실제보다 1 크게 나온다. 슬롯 상한은
    DB 표본 쪽을 쓴다.
    """
    limits: dict[str, float] = {}
    for sample in run.samples_of("pipeline-db"):
        stage = sample.get("stage")
        occupied = (to_float(sample.get("dispatched_count")) or 0.0) + (
            to_float(sample.get("running_count")) or 0.0
        )
        if stage is None:
            continue
        limits[stage] = max(limits.get(stage, 0.0), occupied)
    return limits


def collect_work_records(run: Run) -> tuple[list[dict[str, Any]], int]:
    """pipeline.work.started/succeeded 쌍을 작업 레코드로 묶는다.

    반환값은 (작업 목록, 끝나지 않은 started 수)다.
    """
    starts: dict[str, dict[str, Any]] = {}
    works: list[dict[str, Any]] = []
    for event in sorted(run.events, key=lambda e: e.get("timestamp_utc") or ""):
        event_type = event.get("event_type")
        work_id = event.get("work_id")
        if event_type == "pipeline.work.started" and work_id:
            starts[work_id] = event
        elif event_type == "pipeline.work.succeeded" and work_id:
            start_event = starts.pop(work_id, None) or {}
            execution_ms = to_float(event.get("execution_ms"))
            queue_wait_ms = to_float(start_event.get("queue_wait_ms"))
            works.append(
                {
                    "work_id": work_id,
                    "stage": event.get("stage"),
                    "video_id": event.get("video_id"),
                    "start": parse_ts(start_event.get("timestamp_utc")),
                    "end": parse_ts(event.get("timestamp_utc")),
                    "execution_seconds": (
                        execution_ms / 1000.0 if execution_ms is not None else None
                    ),
                    "queue_wait_seconds": (
                        queue_wait_ms / 1000.0 if queue_wait_ms is not None else None
                    ),
                    "attempt": event.get("work_attempt"),
                }
            )
    return works, len(starts)


def summarize_stage_works(
    works: list[dict[str, Any]], running_limits: dict[str, float]
) -> dict[str, Any]:
    """stage별 실행시간·Queue 대기·span·slot 점유율."""
    stages: dict[str, Any] = {}
    for stage in REFACTORED_STAGES:
        rows = [w for w in works if w["stage"] == stage]
        exec_values = [w["execution_seconds"] for w in rows]
        wait_values = [w["queue_wait_seconds"] for w in rows]
        span_start = min((w["start"] for w in rows if w["start"]), default=None)
        span_end = max((w["end"] for w in rows if w["end"]), default=None)
        span_seconds = (
            (span_end - span_start).total_seconds() if span_start and span_end else None
        )
        limit = running_limits.get(stage)
        execution_sum = sum(v for v in exec_values if v is not None)
        stages[stage] = {
            "measurement": (
                "pipeline.work.started → pipeline.work.succeeded (작업 단위, 겹칠 수 있음)"
            ),
            "executions": len(rows),
            "execution_seconds": describe(
                exec_values, missing=sum(1 for v in exec_values if v is None)
            ),
            "queue_wait_seconds": describe(
                wait_values, missing=sum(1 for v in wait_values if v is None)
            ),
            "max_concurrent_executions_by_event_overlap": max_overlap(
                [(w["start"], w["end"]) for w in rows if w["start"] and w["end"]]
            ),
            "observed_running_limit_from_db_samples": limit,
            "stage_span_start": iso(span_start),
            "stage_span_end": iso(span_end),
            "stage_span_seconds": span_seconds,
            # slot 점유율 = 실행시간 합 / (DB 표본 기준 슬롯 상한 × stage span)
            "slot_utilization_over_span": (
                execution_sum / (limit * span_seconds) if limit and span_seconds else None
            ),
        }
    return stages


def group_events_by_operation(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in events:
        grouped[event.get("operation")].append(event)
    return grouped


def summarize_operation_totals(
    grouped: dict[str, list[dict[str, Any]]], fixture_by_video: dict[str, str]
) -> dict[str, Any]:
    """작업 종류별 건당 시간과 fixture별 영상당 합계."""
    result: dict[str, Any] = {}
    for operation, rows in sorted(grouped.items()):
        per_video_total = _sum_by_video(rows, "duration_ms")
        per_video_count: collections.Counter[str] = collections.Counter()
        for event in rows:
            video_id = event.get("video_id")
            if video_id and video_id != "-":
                per_video_count[video_id] += 1
        by_fixture: dict[str, Any] = {}
        for fixture in sorted({fixture_by_video.get(v) for v in per_video_total} - {None}):
            by_fixture[fixture] = {
                "per_video_total_seconds": describe(
                    [
                        total
                        for video_id, total in per_video_total.items()
                        if fixture_by_video.get(video_id) == fixture
                    ]
                ),
                "operations_per_video": describe(
                    [
                        count
                        for video_id, count in per_video_count.items()
                        if fixture_by_video.get(video_id) == fixture
                    ]
                ),
            }
        result[operation] = {
            "count": len(rows),
            "per_operation_seconds": describe(
                [(to_float(r.get("duration_ms")) or 0.0) / 1000.0 for r in rows]
            ),
            "by_fixture": by_fixture,
        }
    return result


def summarize_ffmpeg_operations(
    ffmpeg_by_op: dict[str, list[dict[str, Any]]], fixture_by_video: dict[str, str]
) -> dict[str, Any]:
    """normalization 내부 분해: probe / audio part / frame candidates."""
    breakdown: dict[str, Any] = {}
    for operation, rows in sorted(ffmpeg_by_op.items()):
        by_fixture: dict[str, Any] = {}
        for fixture in sorted(
            {fixture_by_video.get(r.get("video_id")) for r in rows} - {None}
        ):
            by_fixture[fixture] = describe(
                [
                    (to_float(r.get("duration_ms")) or 0) / 1000.0
                    for r in rows
                    if fixture_by_video.get(r.get("video_id")) == fixture
                ]
            )
        breakdown[operation] = {
            "count": len(rows),
            "seconds": describe(
                [(to_float(r.get("duration_ms")) or 0) / 1000.0 for r in rows]
            ),
            "by_fixture": by_fixture,
        }
    return breakdown


def media_ready_delays(run: Run, works: list[dict[str, Any]]) -> list[float]:
    """NORMALIZE_VIDEO 작업 시작 → gcs.media_input.ready 까지의 시간."""
    start_by_video = {
        w["video_id"]: w["start"]
        for w in works
        if w["stage"] == "NORMALIZE_VIDEO" and w["video_id"] and w["start"]
    }
    delays: list[float] = []
    for event in run.events_of("gcs.media_input.ready"):
        ready_at = parse_ts(event.get("timestamp_utc"))
        start_at = start_by_video.get(event.get("video_id"))
        if ready_at and start_at:
            delays.append((ready_at - start_at).total_seconds())
    return delays


def frame_seek_share(
    works: list[dict[str, Any]],
    ffmpeg_by_op: dict[str, list[dict[str, Any]]],
    fixture_by_video: dict[str, str],
) -> dict[str, Any]:
    """normalization 실행시간에서 프레임 후보 추출이 차지하는 비중."""
    frame_events = ffmpeg_by_op.get("extract_frame_candidates", [])
    frame_seconds = _sum_by_video(frame_events, "duration_ms")
    frame_counts: dict[str, int] = {}
    for event in frame_events:
        video_id = event.get("video_id")
        if video_id and video_id != "-":
            frame_counts[video_id] = int(event.get("frame_count") or 0)

    per_video: list[dict[str, Any]] = []
    for work in works:
        if work["stage"] != "NORMALIZE_VIDEO" or not work["video_id"]:
            continue
        video_id = work["video_id"]
        total = work["execution_seconds"]
        frame = frame_seconds.get(video_id)
        per_video.append(
            {
                "video_id": video_id,
                "fixture": fixture_by_video.get(video_id),
                "normalization_execution_seconds": total,
                "frame_extract_seconds": frame,
                "frame_count": frame_counts.get(video_id),
                "frame_share_of_normalization": (
                    frame / total if frame is not None and total else None
                ),
            }
        )

    by_fixture: dict[str, Any] = {}
    for fixture in sorted({r["fixture"] for r in per_video} - {None}):
        rows = [r for r in per_video if r["fixture"] == fixture]
        by_fixture[fixture] = {
            field: describe([r[field] for r in rows])
            for field in (
                "normalization_execution_seconds",
                "frame_extract_seconds",
                "frame_share_of_normalization",
                "frame_count",
            )
        }
    return {"per_video": per_video, "by_fixture": by_fixture}


def assembly_summary(run: Run) -> dict[str, Any]:
    """assembly는 별도 stage work가 아니라 STT part 완료 뒤 호출된다."""
    succeeded = run.events_of("assembly.succeeded")
    skipped = run.events_of("assembly.skipped")
    retrying = run.events_of("assembly.retrying")
    return {
        "succeeded": len(succeeded),
        "skipped": len(skipped),
        "retrying": len(retrying),
        "succeeded_seconds": describe(
            [(to_float(e.get("duration_ms")) or 0) / 1000.0 for e in succeeded]
        ),
        "skip_outcomes": dict(collections.Counter(e.get("outcome") for e in skipped)),
        "retry_reasons": dict(collections.Counter(e.get("reason") for e in retrying)),
    }


def enrichment_summary(run: Run, fixture_by_video: dict[str, str]) -> dict[str, Any]:
    """enrichment 내부: keyframe 준비와 Vision 요청."""
    keyframe = run.events_of("enrichment.keyframe.prepared")
    vision_ok = run.events_of("vision.request.succeeded")
    fixtures = sorted({v for v in fixture_by_video.values()})
    vision_totals = _sum_by_video(vision_ok, "vision_request_ms")
    return {
        "keyframe_prepared": len(keyframe),
        "keyframe_gcs_download_seconds": describe(
            [(to_float(e.get("gcs_download_ms")) or 0) / 1000.0 for e in keyframe]
        ),
        "keyframe_gcs_upload_seconds": describe(
            [(to_float(e.get("gcs_upload_ms")) or 0) / 1000.0 for e in keyframe]
        ),
        "keyframe_reused_count": sum(1 for e in keyframe if e.get("keyframe_reused")),
        "vision_request_seconds": describe(
            [(to_float(e.get("vision_request_ms")) or 0) / 1000.0 for e in vision_ok]
        ),
        "vision_request_seconds_by_fixture": {
            fixture: describe(
                [
                    (to_float(e.get("vision_request_ms")) or 0) / 1000.0
                    for e in vision_ok
                    if fixture_by_video.get(e.get("video_id")) == fixture
                ]
            )
            for fixture in fixtures
        },
        "vision_per_video_total_seconds_by_fixture": {
            fixture: describe(
                [
                    total
                    for video_id, total in vision_totals.items()
                    if fixture_by_video.get(video_id) == fixture
                ]
            )
            for fixture in fixtures
        },
    }


def per_video_work_spans(run: Run, works: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """영상별 작업 span과 embedding tail.

    EMBED_BATCH 작업은 여러 영상이 공유해 video_id가 남지 않으므로
    span은 NORMALIZE_VIDEO~ENRICH_CHUNK 구간만 포함한다.
    """
    last_enrichment: dict[str, dt.datetime] = {}
    for event in run.events_of("enrichment.completed"):
        video_id = event.get("video_id")
        timestamp = parse_ts(event.get("timestamp_utc"))
        if not video_id or video_id == "-" or timestamp is None:
            continue
        if video_id not in last_enrichment or timestamp > last_enrichment[video_id]:
            last_enrichment[video_id] = timestamp

    spans: list[dict[str, Any]] = []
    for video in run.videos:
        video_id = video["video_id"]
        rows = [w for w in works if w["video_id"] == video_id and w["start"] and w["end"]]
        if not rows:
            continue
        enrichment_done = last_enrichment.get(video_id)
        terminal = video["_terminal"]
        spans.append(
            {
                "video_id": video_id,
                "fixture": video["fixture"],
                "work_count": len(rows),
                "span_seconds": (
                    max(r["end"] for r in rows) - min(r["start"] for r in rows)
                ).total_seconds(),
                "execution_sum_seconds": sum(r["execution_seconds"] or 0.0 for r in rows),
                # 마지막 enrichment 완료 → terminal. embedding 대기 + 실행 구간이다.
                "embedding_tail_seconds": (
                    (terminal - enrichment_done).total_seconds()
                    if terminal and enrichment_done
                    else None
                ),
            }
        )
    return spans


def refactored_stage_block(run: Run) -> dict[str, Any]:
    """pipeline.work.started/succeeded 쌍과 execution_ms로 stage를 본다.

    리팩토링 stage는 영상 간·stage 간 겹치므로 stage 시간의 합은
    영상 처리시간이 아니다. stage span(가장 이른 시작~가장 늦은 종료)을 함께 낸다.
    """
    works, unfinished = collect_work_records(run)
    fixture_by_video = {v["video_id"]: v["fixture"] for v in run.videos}
    ffmpeg_by_op = group_events_by_operation(run.events_of("ffmpeg.operation.succeeded"))
    gcs_by_op = group_events_by_operation(run.events_of("gcs.operation.succeeded"))
    spans = per_video_work_spans(run, works)

    return {
        "schema": "refactored",
        "work_records": len(works),
        "unfinished_work_started": unfinished,
        "stages": summarize_stage_works(works, stage_running_limits(run)),
        "normalization_breakdown": summarize_ffmpeg_operations(
            ffmpeg_by_op, fixture_by_video
        ),
        "gcs_breakdown": summarize_operation_totals(gcs_by_op, fixture_by_video),
        "gcs_media_input_ready_seconds": describe(media_ready_delays(run, works)),
        "frame_seek_share": frame_seek_share(works, ffmpeg_by_op, fixture_by_video),
        "assembly": assembly_summary(run),
        "enrichment_breakdown": enrichment_summary(run, fixture_by_video),
        "per_video_span": spans,
        "per_video_span_note": (
            "EMBED_BATCH 작업은 여러 영상이 공유해 video_id가 남지 않으므로 "
            "이 span은 NORMALIZE_VIDEO~ENRICH_CHUNK 구간만 포함한다."
        ),
        "per_video_span_summary": {
            fixture: {
                "span_seconds": describe(
                    [r["span_seconds"] for r in spans if r["fixture"] == fixture]
                ),
                "embedding_tail_seconds": describe(
                    [
                        r["embedding_tail_seconds"]
                        for r in spans
                        if r["fixture"] == fixture
                    ]
                ),
            }
            for fixture in sorted({r["fixture"] for r in spans})
        },
    }


def _sum_by_video(events: list[dict[str, Any]], field: str) -> dict[str, float]:
    totals: dict[str, float] = collections.defaultdict(float)
    for event in events:
        video_id = event.get("video_id")
        if not video_id or video_id == "-":
            continue
        totals[video_id] += (to_float(event.get(field)) or 0.0) / 1000.0
    return dict(totals)


def stt_block(run: Run) -> dict[str, Any]:
    started = run.events_of("stt.request.started")
    succeeded = run.events_of("stt.request.succeeded")
    durations = [(to_float(e.get("duration_ms")) or 0) / 1000.0 for e in succeeded]
    segments = [to_float(e.get("segments") or e.get("segment_count")) for e in succeeded]
    return {
        "requests_started": len(started),
        "requests_succeeded": len(succeeded),
        "request_seconds": describe(durations),
        "segments_per_request": describe(
            segments, missing=sum(1 for s in segments if s is None)
        ),
        "word_offset_corrections": len(run.events_of("stt.word_offsets.corrected")),
        "word_offset_block_realignments": len(
            run.events_of("stt.word_offsets.block_realigned")
        ),
    }


def embedding_block(run: Run) -> dict[str, Any]:
    success = run.events_of("embedding.request.success")
    retry = run.events_of("embedding.request.retry")
    admission = run.events_of("embedding.admission")
    http = run.events_of("http.request.completed")

    admission_results = collections.Counter(a.get("admission_result") for a in admission)
    waits = [(to_float(a.get("queue_wait_ms")) or 0) / 1000.0 for a in admission]
    inference = [
        to_float(a.get("inference_duration_ms")) for a in admission
    ]
    depth = [to_float(a.get("video_preprocess_queue_depth")) for a in admission]

    by_batch: dict[str, Any] = {}
    for size in sorted({int(e.get("text_count") or 0) for e in success}):
        rows = [e for e in success if int(e.get("text_count") or 0) == size]
        admission_rows = [
            a for a in admission if int(a.get("batch_size") or 0) == size
        ]
        granted_rows = [
            a for a in admission_rows if a.get("admission_result") == "granted"
        ]
        by_batch[str(size)] = {
            "requests": len(rows),
            "texts": size * len(rows),
            # Worker가 잰 값. 여기에는 endpoint admission 대기가 포함된다.
            "worker_observed_request_seconds": describe(
                [(to_float(e.get("duration_ms")) or 0) / 1000.0 for e in rows]
            ),
            # endpoint가 잰 값. slot을 받은 뒤의 순수 추론시간이다.
            "endpoint_inference_seconds": describe(
                [
                    v / 1000.0
                    for v in (
                        to_float(a.get("inference_duration_ms")) for a in granted_rows
                    )
                    if v is not None
                ]
            ),
            "endpoint_admission_wait_seconds": describe(
                [(to_float(a.get("queue_wait_ms")) or 0) / 1000.0 for a in admission_rows]
            ),
        }

    status_codes = collections.Counter(h.get("status_code") for h in http)

    return {
        "requests_success": len(success),
        "requests_retry": len(retry),
        "retry_status_codes": dict(
            collections.Counter(e.get("status_code") for e in retry)
        ),
        "retry_error_codes": dict(collections.Counter(e.get("error_code") for e in retry)),
        "request_seconds": describe(
            [(to_float(e.get("duration_ms")) or 0) / 1000.0 for e in success]
        ),
        "by_batch_size": by_batch,
        "admission_events": len(admission),
        "admission_results": dict(admission_results),
        "admission_queue_wait_seconds": describe(waits),
        "admission_inference_seconds": describe(
            [v / 1000.0 for v in inference if v is not None],
            missing=sum(1 for v in inference if v is None),
        ),
        "admission_video_preprocess_queue_depth": describe(
            depth, missing=sum(1 for v in depth if v is None)
        ),
        "endpoint_http_status_counts": {str(k): v for k, v in status_codes.items()},
    }


# --------------------------------------------------------------------------- #
# Queue
# --------------------------------------------------------------------------- #
def backlog_trend(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """대기열이 늘었다가 줄어드는지, 실행 끝까지 남는지 본다.

    표본을 시간순으로 정렬해 앞·중간·뒤 1/3 구간의 평균을 비교하고,
    마지막 표본 5개의 값과 마지막으로 0이 된 시각을 함께 남긴다.
    """
    ordered = sorted(
        (
            (parse_ts(r.get("timestamp_utc")), to_float(r.get(field)))
            for r in rows
        ),
        key=lambda x: x[0] or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
    )
    values = [(t, v) for t, v in ordered if t is not None and v is not None]
    if not values:
        return {"samples": 0}
    size = len(values)
    third = max(size // 3, 1)
    segments = [values[:third], values[third : 2 * third], values[2 * third :]]
    last_zero = None
    for timestamp, value in values:
        if value == 0:
            last_zero = timestamp
    return {
        "samples": size,
        "mean_first_third": statistics.fmean(v for _, v in segments[0]),
        "mean_middle_third": statistics.fmean(v for _, v in segments[1]),
        "mean_last_third": statistics.fmean(v for _, v in segments[2]) if segments[2] else None,
        "last_five_values": [v for _, v in values[-5:]],
        "final_value": values[-1][1],
        "drained_to_zero_at_end": values[-1][1] == 0,
        "last_zero_at": iso(last_zero),
    }


def summarize_pgmq_queues(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """pgmq Queue별 ready·invisible·oldest age 분포.

    구버전은 `queue`/`ready`/`oldest_age_sec`, 리팩토링은
    `queue_name`/`ready_count`/`oldest_message_age_seconds`를 쓴다.
    """
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for sample in samples:
        grouped[sample.get("queue_name") or sample.get("queue")].append(sample)

    result: dict[str, Any] = {}
    for name, rows in sorted(grouped.items()):
        ready = [to_float(r.get("ready_count", r.get("ready"))) for r in rows]
        invisible = [to_float(r.get("invisible_count", r.get("invisible"))) for r in rows]
        oldest = [
            to_float(r.get("oldest_message_age_seconds", r.get("oldest_age_sec")))
            for r in rows
        ]
        ready_clean = [v for v in ready if v is not None]
        oldest_positive = [
            o for r, o in zip(ready, oldest) if r is not None and r > 0 and o is not None
        ]
        result[name] = {
            "samples": len(rows),
            "ready": describe(ready, missing=sum(1 for v in ready if v is None)),
            "invisible": describe(
                invisible, missing=sum(1 for v in invisible if v is None)
            ),
            "oldest_message_age_seconds": describe(
                oldest, missing=sum(1 for v in oldest if v is None)
            ),
            "oldest_age_when_ready_positive_seconds": describe(oldest_positive),
            "fraction_samples_with_ready_backlog": fraction_at_or_above(ready_clean, 1.0),
            "backlog_trend": backlog_trend(
                rows, "ready_count" if "ready_count" in rows[0] else "ready"
            ),
        }
    return result


def queue_block(run: Run, window: tuple[dt.datetime | None, dt.datetime | None]) -> dict[str, Any]:
    start, end = window

    def in_window(sample: dict[str, Any]) -> bool:
        timestamp = parse_ts(sample.get("timestamp_utc"))
        if timestamp is None:
            return False
        if start and timestamp < start:
            return False
        if end and timestamp > end:
            return False
        return True

    pgmq = summarize_pgmq_queues(
        [s for s in run.samples_of("pgmq") if in_window(s)]
    )

    stage_work: dict[str, Any] = {}
    grouped_stage: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for sample in run.samples_of("pipeline-db"):
        if not in_window(sample):
            continue
        grouped_stage[sample.get("stage")].append(sample)
    for name, rows in sorted(grouped_stage.items()):
        ready = [to_float(r.get("ready_count")) for r in rows]
        # DISPATCHED는 이미 슬롯을 잡았지만 RUNNING 표시 전인 상태다.
        # 슬롯 점유 판정은 dispatched + running으로 해야 과소평가하지 않는다.
        dispatched = [to_float(r.get("dispatched_count")) for r in rows]
        running_only = [to_float(r.get("running_count")) for r in rows]
        running = [
            (d or 0.0) + (r or 0.0) if (d is not None or r is not None) else None
            for d, r in zip(dispatched, running_only)
        ]
        oldest = [to_float(r.get("oldest_ready_age_seconds")) for r in rows]
        failed = [to_float(r.get("failed_count")) for r in rows]
        ready_clean = [v for v in ready if v is not None]
        running_clean = [v for v in running if v is not None]
        oldest_positive = [
            o for r, o in zip(ready, oldest) if r is not None and r > 0 and o is not None
        ]
        peak_running = max(running_clean) if running_clean else None
        # 대기 작업이 있는데도 실행 슬롯이 비어 있었는지 본다.
        backlog_running = [
            r for r, w in zip(running, ready) if r is not None and w is not None and w > 0
        ]
        idle_slot_with_backlog = (
            sum(1 for r in backlog_running if peak_running and r < peak_running)
            / len(backlog_running)
            if backlog_running
            else None
        )
        stage_work[name] = {
            "samples": len(rows),
            "ready": describe(ready, missing=sum(1 for v in ready if v is None)),
            "running_definition": "dispatched_count + running_count (슬롯 점유 기준)",
            "running": describe(running, missing=sum(1 for v in running if v is None)),
            "running_only": describe(
                running_only, missing=sum(1 for v in running_only if v is None)
            ),
            "dispatched": describe(
                dispatched, missing=sum(1 for v in dispatched if v is None)
            ),
            "failed": describe(failed, missing=sum(1 for v in failed if v is None)),
            "oldest_ready_age_seconds": describe(
                oldest, missing=sum(1 for v in oldest if v is None)
            ),
            "oldest_ready_age_when_backlog_seconds": describe(oldest_positive),
            "fraction_samples_with_ready_backlog": fraction_at_or_above(ready_clean, 1.0),
            "observed_running_limit": peak_running,
            "fraction_samples_at_running_limit": (
                fraction_at_or_above(running_clean, peak_running)
                if peak_running
                else None
            ),
            "fraction_samples_saturated": (
                (
                    sum(
                        1
                        for r, w in zip(running, ready)
                        if r is not None
                        and w is not None
                        and peak_running
                        and r >= peak_running
                        and w > 0
                    )
                    / len(rows)
                )
                if rows and peak_running
                else None
            ),
            "running_when_backlog_present": describe(backlog_running),
            "fraction_idle_slot_while_backlog": idle_slot_with_backlog,
            "backlog_trend": backlog_trend(rows, "ready_count"),
        }

    return {
        "window_start": iso(start),
        "window_end": iso(end),
        "pgmq": pgmq,
        "stage_work": stage_work,
    }


# --------------------------------------------------------------------------- #
# 자원
# --------------------------------------------------------------------------- #
def resource_block(run: Run, window: tuple[dt.datetime | None, dt.datetime | None]) -> dict[str, Any]:
    start, end = window

    def collect(source: str, field: str) -> list[tuple[dt.datetime, float]]:
        points: list[tuple[dt.datetime, float]] = []
        for sample in run.samples_of(source):
            timestamp = parse_ts(sample.get("timestamp_utc"))
            if timestamp is None:
                continue
            if start and timestamp < start:
                continue
            if end and timestamp > end:
                continue
            value = to_float(sample.get(field))
            if value is None:
                continue
            points.append((timestamp, value))
        return points

    def series(source: str, field: str, thresholds: tuple[float, ...] | None = None) -> dict[str, Any]:
        points = collect(source, field)
        values = [v for _, v in points]
        total_samples = sum(
            1
            for s in run.samples_of(source)
            if (lambda t: t is not None and (not start or t >= start) and (not end or t <= end))(
                parse_ts(s.get("timestamp_utc"))
            )
        )
        block = describe(values, missing=total_samples - len(values))
        block["time_weighted_mean"] = time_weighted_mean(points)
        if block["mean"] is not None and block["time_weighted_mean"] is not None:
            block["sample_vs_time_weighted_delta"] = block["time_weighted_mean"] - block["mean"]
        else:
            block["sample_vs_time_weighted_delta"] = None
        if thresholds:
            block["fraction_at_or_above"] = {
                str(t): fraction_at_or_above(values, t) for t in thresholds
            }
            block["fraction_below_idle_threshold"] = (
                (sum(1 for v in values if v < IDLE_CPU_THRESHOLD) / len(values))
                if values
                else None
            )
        if len(points) >= 2:
            gaps = [
                (points[i + 1][0] - points[i][0]).total_seconds()
                for i in range(len(points) - 1)
            ]
            block["sample_interval_seconds"] = describe(gaps)
        return block

    cloud_run = {
        "worker_cpu_percent": series("cloud-monitoring", "worker_cpu_percent", CPU_THRESHOLDS),
        "worker_memory_percent": series("cloud-monitoring", "worker_memory_percent"),
        "worker_instance_count": series("cloud-monitoring", "worker_instance_count"),
    }
    worker_process = {
        "cpu_percent": series("worker-process", "cpu_percent", CPU_THRESHOLDS),
        "rss_bytes": series("worker-process", "rss_bytes"),
    }
    embedding_vm = {
        "host_cpu_percent": series("embedding-vm", "cpu_percent", CPU_THRESHOLDS),
        "host_memory_percent": series("embedding-vm", "memory_percent"),
        "container_cpu_percent": series("embedding-vm", "container_cpu_percent", CPU_THRESHOLDS),
        "container_memory_percent": series("embedding-vm", "container_memory_percent"),
        "container_memory_bytes": series("embedding-vm", "container_memory_bytes"),
        "endpoint_process_cpu_percent": series(
            "embedding-vm", "endpoint_process_cpu_percent", CPU_THRESHOLDS
        ),
        "endpoint_process_memory_bytes": series(
            "embedding-vm", "endpoint_process_memory_bytes"
        ),
        "network_bytes_per_second": series("embedding-vm", "network_bytes_per_second"),
    }

    container_memory = embedding_vm["container_memory_bytes"]["max"]
    endpoint_memory = embedding_vm["endpoint_process_memory_bytes"]["max"]
    embedding_vm["container_minus_endpoint_max_memory_bytes"] = (
        container_memory - endpoint_memory
        if container_memory is not None and endpoint_memory is not None
        else None
    )

    summary = run.environment.get("target_vm_summary") or {}
    return {
        "window_start": iso(start),
        "window_end": iso(end),
        "cloud_run_worker": cloud_run,
        "worker_process": worker_process,
        "embedding_vm": embedding_vm,
        "embedding_vm_restart_flags": {
            "vm_restart_detected": summary.get("vm_restart_detected"),
            "container_restart_detected": summary.get("container_restart_detected"),
            "container_not_running_samples": summary.get("container_not_running_samples"),
            "initial_boot_id": summary.get("initial_boot_id"),
            "final_boot_id": summary.get("final_boot_id"),
            "initial_container_id": summary.get("initial_container_id"),
            "final_container_id": summary.get("final_container_id"),
        },
    }


# --------------------------------------------------------------------------- #
# 신뢰성
# --------------------------------------------------------------------------- #
def reliability_block(run: Run) -> dict[str, Any]:
    terminal = collections.Counter(v.get("terminal_status") for v in run.videos)
    timing_status = collections.Counter(
        (v.get("pipeline_timing") or {}).get("status") for v in run.videos
    )

    retryable = run.events_of("pipeline.work.retryable_failed")
    retryable_detail = [
        {
            "stage": e.get("stage"),
            "video_id": e.get("video_id"),
            "failure_code": e.get("failure_code"),
            "work_id": e.get("work_id"),
            "timestamp_utc": e.get("timestamp_utc"),
            "chunk_index": e.get("chunk_index"),
        }
        for e in retryable
    ]

    # 재시도 후 같은 작업이 최종 성공했는지 확인한다.
    succeeded_work_ids = {
        e.get("work_id") for e in run.events_of("pipeline.work.succeeded")
    }
    retry_recovered = sum(
        1 for e in retryable if e.get("work_id") in succeeded_work_ids
    )
    # work_id가 다르게 재발행되는 경우를 위해 (stage, video_id, chunk_index)로도 확인.
    succeeded_keys = {
        (e.get("stage"), e.get("video_id"), e.get("chunk_index"))
        for e in run.events_of("pipeline.work.succeeded")
    }
    retry_recovered_by_key = sum(
        1
        for e in retryable
        if (e.get("stage"), e.get("video_id"), e.get("chunk_index")) in succeeded_keys
    )

    vision_failed = run.events_of("vision.provider.failed")
    vision_ok = run.events_of("vision.provider.succeeded")

    embedding_retry = run.events_of("embedding.request.retry")

    http_codes = collections.Counter(
        h.get("status_code") for h in run.events_of("http.request.completed")
    )

    summary = run.environment.get("target_vm_summary") or {}

    enrichment_completed = run.events_of("enrichment.completed")
    vision_attempts = len(vision_ok) + len(vision_failed)

    # chunk 단위로 vision 성공 여부를 확인한다. 작업 재시도 때문에 이벤트 수는
    # chunk 수보다 클 수 있으므로 (video_id, chunk_index) 집합으로 비교한다.
    vision_ok_chunks = {
        (e.get("video_id"), e.get("chunk_index"))
        for e in run.events_of("vision.request.succeeded")
    }
    completed_chunks = {
        (e.get("video_id"), e.get("chunk_index")) for e in enrichment_completed
    }
    chunks_without_vision = sorted(completed_chunks - vision_ok_chunks, key=str)

    return {
        "terminal_status_counts": dict(terminal),
        "pipeline_timing_status_counts": dict(timing_status),
        "final_success": terminal.get("READY", 0),
        "final_failure": sum(v for k, v in terminal.items() if k != "READY"),
        "missing_terminal": sum(1 for v in run.videos if v["_terminal"] is None),
        "retryable_failures": len(retryable),
        "retryable_failure_detail": retryable_detail,
        "retryable_failure_codes": dict(
            collections.Counter(e.get("failure_code") for e in retryable)
        ),
        "retryable_recovered_same_work_id": retry_recovered,
        "retryable_recovered_by_stage_video_chunk": retry_recovered_by_key,
        "assembly_retrying": len(run.events_of("assembly.retrying")),
        "assembly_succeeded": len(run.events_of("assembly.succeeded")),
        "assembly_skipped": len(run.events_of("assembly.skipped")),
        "vision_provider_succeeded": len(vision_ok),
        "vision_provider_failed": len(vision_failed),
        "vision_failure_status_codes": dict(
            collections.Counter(e.get("status_code") for e in vision_failed)
        ),
        "vision_failure_codes": dict(
            collections.Counter(e.get("failure_code") for e in vision_failed)
        ),
        "vision_attempts": vision_attempts,
        "enrichment_completed": len(enrichment_completed),
        "enrichment_chunks_completed": len(completed_chunks),
        "enrichment_chunks_with_vision_success": len(
            completed_chunks & vision_ok_chunks
        ),
        "enrichment_chunks_without_vision_success": len(chunks_without_vision),
        "enrichment_chunks_without_vision_detail": chunks_without_vision[:20],
        "embedding_request_retries": len(embedding_retry),
        "embedding_retry_status_codes": dict(
            collections.Counter(e.get("status_code") for e in embedding_retry)
        ),
        "embedding_endpoint_http_status_counts": {
            str(k): v for k, v in http_codes.items()
        },
        "embedding_vm_restart_detected": summary.get("vm_restart_detected"),
        "embedding_container_restart_detected": summary.get("container_restart_detected"),
        "worker_instance_count_max": None,  # resource 블록에서 채운다.
        "observability_status": run.environment.get("observability_status"),
        "observability_errors": run.environment.get("observability_errors"),
    }


def raw_log_error_scan(run: Run) -> dict[str, Any]:
    """raw-logs.tar.gz 안에서 예외 클래스 이름 빈도만 센다 (읽기 전용).

    숫자 상태코드(429·503)는 UUID·바이트 수 같은 다른 숫자에도 걸려
    substring 방식으로는 신뢰할 수 없다. 그래서 이름이 명확한 예외만 센다.
    상태코드 집계는 구조화 이벤트(`http.request.completed`,
    `vision.provider.failed`)를 쓴다.
    """
    path = os.path.join(run.path, "raw-logs.tar.gz")
    keywords = [
        "DBAPIError",
        "OperationalError",
        "ReadTimeout",
        "TimeoutError",
        "DeadlockDetected",
        "deadlock detected",
        "Traceback (most recent call last)",
    ]
    counts: collections.Counter[str] = collections.Counter()
    files = 0
    if not os.path.exists(path):
        return {"available": False}
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                files += 1
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", errors="replace")
                for keyword in keywords:
                    hits = text.count(keyword)
                    if hits:
                        counts[keyword] += hits
    except (tarfile.TarError, OSError) as error:
        return {"available": False, "error": str(error)}
    return {"available": True, "member_files": files, "keyword_counts": dict(counts)}


# --------------------------------------------------------------------------- #
# 실행 단위 집계
# --------------------------------------------------------------------------- #
def analyze_run(key: str, path: str) -> dict[str, Any]:
    run = Run(key, path)
    contract = contract_block(run)
    latency = latency_block(run)
    workload = workload_block(run)
    throughput = throughput_block(run, latency, workload)
    window = run.active_window
    result = {
        "key": key,
        "contract": contract,
        "latency": latency,
        "fairness": fairness_block(run, latency),
        "workload": workload,
        "throughput": throughput,
        "stages": stage_block(run),
        "stt": stt_block(run),
        "embedding": embedding_block(run),
        "queues": queue_block(run, window),
        "resources": resource_block(run, window),
        "reliability": reliability_block(run),
        "raw_log_scan": raw_log_error_scan(run),
    }
    result["reliability"]["worker_instance_count_max"] = (
        result["resources"]["cloud_run_worker"]["worker_instance_count"]["max"]
    )
    return result


# --------------------------------------------------------------------------- #
# 비교
# --------------------------------------------------------------------------- #
def pick(source: dict[str, Any], *path: str) -> Any:
    """중첩 dict에서 경로를 따라 값을 꺼낸다. 없으면 None."""
    node: Any = source
    for part in path:
        if node is None:
            return None
        node = node.get(part) if isinstance(node, dict) else None
    return node


def comparison_row(
    old: dict[str, Any],
    new: dict[str, Any],
    path: tuple[str, ...],
    lower_is_better: bool,
    note: str = "",
) -> dict[str, Any]:
    """구버전·리팩토링 한 지표의 값과 상대 변화."""
    old_value = pick(old, *path)
    new_value = pick(new, *path)
    change = ratio_change(new_value, old_value)
    return {
        "path": ".".join(path),
        "legacy": old_value,
        "refactored": new_value,
        "relative_change": change,
        "lower_is_better": lower_is_better,
        "improved": (
            None if change is None else (change < 0 if lower_is_better else change > 0)
        ),
        "note": note,
    }


def work_adjusted_throughput(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any] | None:
    """embedding text 수가 다를 때 작업량을 맞춘 처리량."""
    old_texts = pick(old, "workload", "embedding_texts_total")
    new_texts = pick(new, "workload", "embedding_texts_total")
    old_rate = pick(old, "throughput", "round_makespan_sum", "videos_per_hour")
    new_rate = pick(new, "throughput", "round_makespan_sum", "videos_per_hour")
    if not all(v is not None for v in (old_texts, new_texts, old_rate, new_rate)):
        return None
    if not new_texts or not old_texts:
        return None
    adjusted_new = new_rate * (new_texts / old_texts)
    return {
        "legacy_videos_per_hour": old_rate,
        "refactored_videos_per_hour_raw": new_rate,
        "refactored_videos_per_hour_text_adjusted": adjusted_new,
        "relative_change_raw": ratio_change(new_rate, old_rate),
        "relative_change_text_adjusted": ratio_change(adjusted_new, old_rate),
        "legacy_embedding_texts": old_texts,
        "refactored_embedding_texts": new_texts,
        "formula": "adjusted = refactored_rate * (refactored_texts / legacy_texts)",
    }


def compare(old: dict[str, Any], new: dict[str, Any], scenario: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}

    def add(name: str, path: tuple[str, ...], lower_is_better: bool, note: str = "") -> None:
        metrics[name] = comparison_row(old, new, path, lower_is_better, note)

    add("throughput_videos_per_hour_active_window",
        ("throughput", "workload_active_window", "videos_per_hour"), False)
    add("throughput_videos_per_hour_round_makespan",
        ("throughput", "round_makespan_sum", "videos_per_hour"), False)
    add("throughput_media_hours_per_hour_round_makespan",
        ("throughput", "round_makespan_sum", "media_hours_per_hour"), False)
    add("throughput_chunks_per_hour_round_makespan",
        ("throughput", "round_makespan_sum", "chunks_per_hour"), False)
    add("throughput_embedding_texts_per_hour_round_makespan",
        ("throughput", "round_makespan_sum", "embedding_texts_per_hour"), False)
    add("round_makespan_mean_seconds",
        ("latency", "round_makespan_seconds", "mean"), True)
    add("round_makespan_max_seconds",
        ("latency", "round_makespan_seconds", "max"), True)
    add("completion_latency_mean_seconds",
        ("latency", "overall", "completion_latency_seconds", "mean"), True)
    add("completion_latency_median_seconds",
        ("latency", "overall", "completion_latency_seconds", "median"), True)
    add("completion_latency_p95_seconds",
        ("latency", "overall", "completion_latency_seconds", "p95"), True)
    add("completion_latency_max_seconds",
        ("latency", "overall", "completion_latency_seconds", "max"), True)
    add("initial_queue_wait_mean_seconds",
        ("latency", "overall", "initial_queue_wait_seconds", "mean"), True)
    add("initial_queue_wait_p95_seconds",
        ("latency", "overall", "initial_queue_wait_seconds", "p95"), True)
    add("initial_queue_wait_max_seconds",
        ("latency", "overall", "initial_queue_wait_seconds", "max"), True)
    add("slowdown_ratio_mean",
        ("latency", "overall", "slowdown_ratio", "mean"), True)

    for fixture in ("medium", "long", "short"):
        if pick(old, "latency", "by_fixture", fixture) is None:
            continue
        for stat in ("mean", "median", "p90", "p95", "max"):
            add(
                f"{fixture}_completion_latency_{stat}_seconds",
                ("latency", "by_fixture", fixture, "completion_latency_seconds", stat),
                True,
            )
        for stat in ("mean", "p95", "max"):
            add(
                f"{fixture}_initial_queue_wait_{stat}_seconds",
                ("latency", "by_fixture", fixture, "initial_queue_wait_seconds", stat),
                True,
            )

    add("worker_cpu_mean_percent",
        ("resources", "cloud_run_worker", "worker_cpu_percent", "mean"), False,
        "높을수록 가용 자원을 더 쓴다는 뜻일 뿐, 그 자체가 성능 개선은 아니다")
    add("worker_cpu_p95_percent",
        ("resources", "cloud_run_worker", "worker_cpu_percent", "p95"), False)
    add("worker_memory_mean_percent",
        ("resources", "cloud_run_worker", "worker_memory_percent", "mean"), False)
    add("embedding_container_cpu_mean_percent",
        ("resources", "embedding_vm", "container_cpu_percent", "mean"), False)
    add("embedding_container_cpu_p95_percent",
        ("resources", "embedding_vm", "container_cpu_percent", "p95"), False)
    add("embedding_admission_wait_mean_seconds",
        ("embedding", "admission_queue_wait_seconds", "mean"), True)
    add("embedding_admission_wait_max_seconds",
        ("embedding", "admission_queue_wait_seconds", "max"), True)
    add("embedding_request_mean_seconds",
        ("embedding", "request_seconds", "mean"), True)
    add("embedding_requests_success",
        ("embedding", "requests_success"), False,
        "요청 수 자체는 개선/퇴행 판정 대상이 아니라 작업 분할 방식의 차이다")
    add("embedding_texts_total",
        ("workload", "embedding_texts_total"), False,
        "작업량 지표. 값이 다르면 처리량 비교에 보정이 필요하다")
    add("chunks_total", ("workload", "chunks_total"), False, "작업량 지표")
    add("stt_request_mean_seconds", ("stt", "request_seconds", "mean"), True)
    add("stt_requests_succeeded", ("stt", "requests_succeeded"), False, "작업량 지표")
    add("final_success_count", ("reliability", "final_success"), False)
    add("embedding_request_retries", ("reliability", "embedding_request_retries"), True)

    cpu_fraction = {}
    for side, source in (("legacy", old), ("refactored", new)):
        cpu_fraction[side] = pick(
            source, "resources", "cloud_run_worker", "worker_cpu_percent", "fraction_at_or_above"
        )
    metrics["worker_cpu_fraction_at_or_above"] = {
        "path": "resources.cloud_run_worker.worker_cpu_percent.fraction_at_or_above",
        "legacy": cpu_fraction["legacy"],
        "refactored": cpu_fraction["refactored"],
        "relative_change": None,
        "lower_is_better": False,
        "improved": None,
        "note": "지속 사용률 비교용. 단일 피크가 아니라 임계 이상 표본 비율로 본다",
    }

    return {
        "scenario": scenario,
        "legacy_run_id": pick(old, "contract", "run_id"),
        "refactored_run_id": pick(new, "contract", "run_id"),
        "metrics": metrics,
        "work_adjusted_throughput": work_adjusted_throughput(old, new),
        "comparability": comparability_notes(old, new),
    }


def comparability_notes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    def env(source: dict[str, Any], key: str) -> Any:
        environment = (source.get("contract") or {}).get("worker_environment") or {}
        return environment.get(key)

    def vm(source: dict[str, Any], key: str) -> Any:
        config = (source.get("contract") or {}).get("embedding_vm_config") or {}
        return config.get(key)

    differences: list[dict[str, Any]] = []

    def diff(name: str, old_value: Any, new_value: Any, classification: str, note: str) -> None:
        if old_value != new_value:
            differences.append(
                {
                    "item": name,
                    "legacy": old_value,
                    "refactored": new_value,
                    "classification": classification,
                    "note": note,
                }
            )

    for key in (
        "STT_PART_CONCURRENCY",
        "EMBEDDING_TIMEOUT_SEC",
        "EMBEDDING_BATCH_SIZE",
        "CHUNK_MAX_TOKENS",
        "WORKER_CONCURRENCY",
    ):
        diff(f"worker.{key}", env(old, key), env(new, key), "교란 요인", "환경변수 차이")
    for key in (
        "EMBEDDING_MAX_LENGTH",
        "MAX_CONCURRENCY",
        "INFERENCE_THREADS",
        "VIDEO_PREPROCESS_REQUEST_LIMIT",
        "VIDEO_PREPROCESS_WAIT_TIMEOUT_SEC",
        "machine_type",
    ):
        diff(f"embedding_vm.{key}", vm(old, key), vm(new, key), "교란 요인", "endpoint 설정 차이")

    diff(
        "timing_schema",
        (old.get("contract") or {}).get("timing_schema"),
        (new.get("contract") or {}).get("timing_schema"),
        "비교 불가",
        "stage 측정 계약이 달라 stage 이름이 같아도 직접 비교하지 않는다",
    )
    diff(
        "chunks_total",
        (old.get("workload") or {}).get("chunks_total"),
        (new.get("workload") or {}).get("chunks_total"),
        "조건부 비교",
        "실제 작업량 차이. 처리량 비교 시 보정값을 함께 본다",
    )
    diff(
        "embedding_texts_total",
        (old.get("workload") or {}).get("embedding_texts_total"),
        (new.get("workload") or {}).get("embedding_texts_total"),
        "조건부 비교",
        "실제 작업량 차이",
    )
    diff(
        "runtime_config_recorded",
        (old.get("contract") or {}).get("runtime_config_recorded"),
        (new.get("contract") or {}).get("runtime_config_recorded"),
        "데이터 계보 한계",
        "구버전 S3는 artifact에 runtime config가 없다",
    )

    fixture_match = (
        (old.get("contract") or {}).get("fixtures_manifest_sha256_by_name")
        == (new.get("contract") or {}).get("fixtures_manifest_sha256_by_name")
    )
    return {
        "fixture_sha256_identical": fixture_match,
        "differences": differences,
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def embedding_length_probe(path: str) -> dict[str, Any]:
    """실행의 embedding endpoint 추론시간과 입력 길이만 뽑는다.

    구버전 S3의 `EMBEDDING_MAX_LENGTH` 값이 artifact runtime config에 없어서,
    같은 입력 길이에서의 추론시간을 확인된 256 실행·확인된 1024 실행과
    대조하는 데 쓴다. 성능 비교 통계에는 넣지 않는다.
    """
    events_path = os.path.join(path, "events.jsonl")
    if not os.path.exists(events_path):
        return {"available": False, "path": path}
    granted: list[dict[str, Any]] = []
    chars: list[float] = []
    waits: list[float] = []
    for event in read_jsonl(events_path):
        if event.get("event_type") != "embedding.admission":
            continue
        value = to_float(event.get("max_text_chars"))
        if value is not None:
            chars.append(value)
        wait = to_float(event.get("queue_wait_ms"))
        if wait is not None:
            waits.append(wait / 1000.0)
        if event.get("admission_result") == "granted":
            granted.append(event)
    payloads: dict[int, list[float]] = collections.defaultdict(list)
    for event in read_jsonl(events_path):
        if event.get("event_type") != "embed.success":
            continue
        size = int(event.get("text_count") or 0)
        value = to_float(event.get("payload_size"))
        if value is not None:
            payloads[size].append(value)

    by_batch: dict[str, Any] = {}
    for size in sorted({int(e.get("batch_size") or 0) for e in granted}):
        values = [
            v / 1000.0
            for v in (
                to_float(e.get("inference_duration_ms"))
                for e in granted
                if int(e.get("batch_size") or 0) == size
            )
            if v is not None
        ]
        by_batch[str(size)] = {
            "inference_seconds": describe(values),
            "request_payload_bytes": describe(payloads.get(size, [])),
        }
    environment_path = os.path.join(path, "environment.json")
    declared = None
    if os.path.exists(environment_path):
        environment = json.load(open(environment_path, encoding="utf-8"))
        declared = (
            ((environment.get("runtime_config") or {}).get("embedding_vm") or {})
            .get("config", {})
            .get("EMBEDDING_MAX_LENGTH")
        )
    return {
        "available": True,
        "path": path,
        "declared_embedding_max_length": declared,
        "max_text_chars": describe(chars),
        "admission_queue_wait_seconds": describe(waits),
        "by_batch_size": by_batch,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--out", default=DEFAULT_METRICS_OUT)
    parser.add_argument(
        "--include-supplementary",
        action="store_true",
        help="중간 설정 실행(embed1 S3)을 보조 자료로 함께 집계한다",
    )
    args = parser.parse_args()

    runs: dict[str, Any] = {}
    inputs: dict[str, str] = {}
    for key, directory in RUN_DIRS.items():
        path = os.path.join(args.artifact_root, directory)
        runs[key] = analyze_run(key, path)
        inputs[key] = path

    supplementary: dict[str, Any] = {}
    if args.include_supplementary:
        for key, directory in SUPPLEMENTARY_RUN_DIRS.items():
            path = os.path.join(args.artifact_root, directory)
            supplementary[key] = analyze_run(key, path)
            inputs[key] = path

    comparisons = {
        "S3": compare(runs["s3_legacy"], runs["s3_refactored"], "S3"),
        "S4": compare(runs["s4_legacy"], runs["s4_refactored"], "S4"),
    }

    document = {
        "formula_version": FORMULA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "percentile_method": "linear interpolation (numpy 'linear' / R type 7)",
        "round_gap_seconds": ROUND_GAP_SECONDS,
        "idle_cpu_threshold_percent": IDLE_CPU_THRESHOLD,
        "cpu_thresholds_percent": list(CPU_THRESHOLDS),
        "input_run_ids": {
            key: runs[key]["contract"]["run_id"] for key in runs
        },
        "input_paths": inputs,
        "definitions": {
            "runner_wall_clock": "environment.started_at → environment.finished_at",
            "workload_active_window": "첫 complete_started_at → 마지막 terminal_observed_at",
            "round_makespan": "회차 첫 complete_started_at → 회차 마지막 terminal_observed_at",
            "completion_latency": "complete_responded_at → terminal_observed_at",
            "initial_queue_wait": (
                "complete_responded_at → 첫 Worker 작업 시작"
                " (구버전 pipeline.stage.started[download],"
                " 리팩토링 pipeline.work.started[NORMALIZE_VIDEO])"
            ),
            "stage_execution_time": (
                "구버전 pipeline.stage.started→finished, "
                "리팩토링 pipeline.work.succeeded.execution_ms"
            ),
            "stage_span": "해당 stage 작업의 가장 이른 시작 → 가장 늦은 종료",
            "slowdown_ratio": "completion_latency / fixture duration",
        },
        "runs": runs,
        "supplementary_runs": supplementary,
        "comparisons": comparisons,
        "embedding_max_length_probe": {
            "purpose": (
                "구버전 S3의 EMBEDDING_MAX_LENGTH가 artifact에 없어서, 같은 입력 길이에서의 "
                "endpoint 추론시간을 확인된 256 실행·확인된 1024 실행과 대조한다. "
                "성능 통계에는 포함하지 않는다."
            ),
            **{
                key: embedding_length_probe(os.path.join(args.artifact_root, directory))
                for key, directory in list(RUN_DIRS.items())
                + [("reference_maxlen256_excluded", MAXLEN_REFERENCE_RUN)]
            },
        },
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, default=str)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
