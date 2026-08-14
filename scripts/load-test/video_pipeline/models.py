from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


FixtureKind = Literal["short", "medium", "long"]


@dataclass(frozen=True)
class DispatchPhase:
    fixture: FixtureKind
    request_count: int
    concurrency: int
    delay_before_seconds: float = 0.0
    wait_for_previous_terminal: bool = False


@dataclass(frozen=True)
class ScenarioOverrides:
    repeat_count: int | None = None
    request_count: int | None = None
    concurrency: int | None = None
    fixture: FixtureKind | None = None
    phase_delay_seconds: float | None = None


@dataclass(frozen=True)
class ScenarioPlan:
    preset: str
    repeat_count: int
    phases: tuple[DispatchPhase, ...]
    is_baseline: bool

    @property
    def requests_per_repeat(self) -> int:
        return sum(phase.request_count for phase in self.phases)

    @property
    def total_requests(self) -> int:
        return self.requests_per_repeat * self.repeat_count


@dataclass(frozen=True)
class PreparedVideo:
    video_id: str
    fixture: FixtureKind
    size_bytes: int


@dataclass(frozen=True)
class FixtureSpec:
    kind: FixtureKind
    path: Path
    sha256: str
    duration_seconds: float
    size_bytes: int


@dataclass(frozen=True)
class CompleteRequestRecord:
    video_id: str
    fixture: FixtureKind
    trace_id: str
    started_at: datetime
    responded_at: datetime
    response_status: str


@dataclass(frozen=True)
class TerminalStatusRecord:
    video_id: str
    status: str
    observed_at: datetime
