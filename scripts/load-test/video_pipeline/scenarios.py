from __future__ import annotations

from infrastructure import LoadTestError
from video_pipeline.models import DispatchPhase, ScenarioOverrides, ScenarioPlan


_SCENARIO_PRESETS = {
    "S1": ScenarioPlan(
        preset="S1",
        repeat_count=3,
        phases=(
            DispatchPhase("short", 1, 1),
            DispatchPhase("medium", 1, 1, wait_for_previous_terminal=True),
            DispatchPhase("long", 1, 1, wait_for_previous_terminal=True),
        ),
        is_baseline=True,
    ),
    "S2": ScenarioPlan(
        preset="S2",
        repeat_count=3,
        phases=(DispatchPhase("medium", 4, 4),),
        is_baseline=True,
    ),
    "S3": ScenarioPlan(
        preset="S3",
        repeat_count=3,
        phases=(DispatchPhase("medium", 8, 8),),
        is_baseline=True,
    ),
    "S4": ScenarioPlan(
        preset="S4",
        repeat_count=3,
        phases=(
            DispatchPhase("long", 4, 4),
            DispatchPhase("short", 4, 4, delay_before_seconds=10.0),
        ),
        is_baseline=True,
    ),
}


#  S1~S4 기본 시나리오와 사용자가 바꾼 값을 합쳐 최종 실행 계획을 생성
# overrides : 변경값
def build_scenario_plan(
    preset: str,
    overrides: ScenarioOverrides | None = None,
) -> ScenarioPlan:
    normalized_preset = preset.strip().upper()
    try:
        baseline_plan = _SCENARIO_PRESETS[normalized_preset]
    except KeyError:
        raise _unknown_preset(preset) from None

    requested = overrides or ScenarioOverrides()  # 변경값이 없으면 각 필드가 None
    repeat_count = (
        requested.repeat_count
        if requested.repeat_count is not None
        else baseline_plan.repeat_count
    )
    phases = tuple(
        _apply_overrides(phase, requested, phase_index)
        for phase_index, phase in enumerate(baseline_plan.phases)
    )
    plan = ScenarioPlan(
        preset=normalized_preset,
        repeat_count=repeat_count,
        phases=phases,
        is_baseline=(
            repeat_count == baseline_plan.repeat_count
            and phases == baseline_plan.phases
        ),
    )
    _validate_plan(plan)
    return plan


def _apply_overrides(
    phase: DispatchPhase,
    overrides: ScenarioOverrides,
    phase_index: int,
) -> DispatchPhase:
    request_count = (
        overrides.request_count
        if overrides.request_count is not None
        else phase.request_count
    )
    concurrency = (
        overrides.concurrency
        if overrides.concurrency is not None
        else min(phase.concurrency, request_count)
    )
    delay_before_seconds = phase.delay_before_seconds
    if phase_index > 0 and overrides.phase_delay_seconds is not None:
        delay_before_seconds = overrides.phase_delay_seconds
    return DispatchPhase(
        fixture=overrides.fixture or phase.fixture,
        request_count=request_count,
        concurrency=concurrency,
        delay_before_seconds=delay_before_seconds,
        wait_for_previous_terminal=phase.wait_for_previous_terminal,
    )


def _validate_plan(plan: ScenarioPlan) -> None:
    if plan.repeat_count <= 0:
        raise LoadTestError("repeat_count must be a positive integer.")
    if not plan.phases:
        raise LoadTestError("scenario must contain at least one dispatch phase.")
    for index, phase in enumerate(plan.phases):
        if phase.request_count <= 0 or phase.concurrency <= 0:
            raise LoadTestError("request_count and concurrency must be positive integers.")
        if phase.concurrency > phase.request_count:
            raise LoadTestError("concurrency cannot exceed request_count.")
        if phase.delay_before_seconds < 0:
            raise LoadTestError("phase_delay_seconds must be non-negative.")
        if index == 0 and phase.delay_before_seconds != 0:
            raise LoadTestError("the first phase cannot have a start delay.")


def _unknown_preset(preset: str) -> LoadTestError:
    choices = ", ".join(_SCENARIO_PRESETS)
    return LoadTestError(f"Unknown video pipeline preset {preset!r}. Choose one of: {choices}.")
