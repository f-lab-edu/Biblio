from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


StepStatus = Literal["PASS", "FAIL", "SKIP"]


@dataclass(frozen=True)
class StepResult:
    name: str
    status: StepStatus
    started_at: str
    finished_at: str
    observations: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ReportWriter:
    def __init__(self, *, run_dir: Path, started_at: str | None = None) -> None:
        self.run_dir = run_dir
        self.started_at = started_at or utc_now()
        self._steps: list[StepResult] = []

    @property
    def steps(self) -> list[StepResult]:
        return list(self._steps)

    def add_step(self, result: StepResult) -> None:
        self._steps.append(result)

    def write(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / "report.json"
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "steps": [asdict(step) for step in self._steps],
        }

    @property
    def status(self) -> StepStatus:
        if any(step.status == "FAIL" for step in self._steps):
            return "FAIL"
        if self._steps and all(step.status == "SKIP" for step in self._steps):
            return "SKIP"
        return "PASS"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_for_path() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
