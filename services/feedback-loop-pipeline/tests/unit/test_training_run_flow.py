from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.evaluation.evaluator import EvaluationResult
from src.run_control.flow import (
    RunFlowService,
    RunRecord,
)
from src.training.runner import TrainingOutput


class _FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 3, 12, 0, tzinfo=UTC)


class _RunStateStore:
    def __init__(self, runs: list[RunRecord]) -> None:
        self.runs = {run.id: run for run in runs}
        self.training_outputs: dict = {}

    async def get_running(self, run_id):
        return self.runs[run_id]

    async def record_training_output(self, run_id, output):
        self.training_outputs[run_id] = output

    async def mark_ready_for_release(self, run_id, evaluation_id):
        self.runs[run_id].status = "READY_FOR_RELEASE"

    async def mark_failed(
        self,
        run_id,
        *,
        failed_stage,
        failure_type,
        failure_reason,
        evaluation_id=None,
    ):
        run = self.runs[run_id]
        run.status = "FAILED"
        run.failed_stage = failed_stage
        run.failure_type = failure_type
        run.failure_reason = failure_reason
        run.evaluation_id = evaluation_id


class _EvaluationRecorder:
    def __init__(self) -> None:
        self.saved_results: list[EvaluationResult] = []

    async def save(self, result):
        evaluation_id = uuid4()
        self.saved_results.append(result)
        return evaluation_id


class _FailingEvaluationRecorder:
    async def save(self, result):
        # Async to satisfy the EvaluationRecorderPort test double contract.
        _ = result
        raise RuntimeError("evaluation storage crashed")


class _HandoffSink:
    def __init__(self) -> None:
        self.calls: list = []

    async def ready_for_release(self, *, run_id, trace_id):
        self.calls.append((run_id, trace_id))


class _FailingHandoffSink:
    def __init__(self) -> None:
        self.calls: list = []

    async def ready_for_release(self, *, run_id, trace_id):
        # Async to satisfy the HandoffSinkPort test double contract.
        self.calls.append((run_id, trace_id))
        raise RuntimeError("handoff crashed")


class _FakeTrainingRunner:
    async def train(self, training_input, *, workspace_dir):
        return TrainingOutput(
            candidate_model_version=training_input.candidate_model_version,
            model_artifact_ref="gs://bucket/model/candidate-v1/model_manifest.json",
            training_metadata_ref="gs://bucket/model/candidate-v1/training_metadata.json",
            completed_at=datetime(2026, 5, 3, 12, 30, tzinfo=UTC),
        )


class _FailingTrainingRunner:
    async def train(self, training_input, *, workspace_dir):
        raise RuntimeError("training crashed")


class _FakeEvaluator:
    def __init__(self, decision: str) -> None:
        self.decision = decision

    async def evaluate(self, *, baseline_model_version, candidate_model_version, evaluation_dataset_ref):
        return EvaluationResult(
            candidate_model_version=candidate_model_version,
            baseline_model_version=baseline_model_version,
            evaluation_dataset_ref=evaluation_dataset_ref,
            sample_count=1,
            quality_metrics={"recall_at_5": 1.0, "mrr_at_5": 1.0, "ndcg_at_5": 1.0}
            if self.decision == "PASS"
            else {"recall_at_5": 0.0, "mrr_at_5": 0.0, "ndcg_at_5": 0.0},
            pass_criteria={"rule": "candidate_metrics_gte_baseline"},
            overall_decision=self.decision,
            fail_reason=None if self.decision == "PASS" else "candidate metrics did not meet baseline",
            details=[],
            evaluated_at=datetime(2026, 5, 3, 12, 40, tzinfo=UTC),
        )


class _FailingEvaluator:
    async def evaluate(self, *, baseline_model_version, candidate_model_version, evaluation_dataset_ref):
        raise RuntimeError("evaluation crashed")


def _make_run() -> RunRecord:
    return RunRecord(
        id=uuid4(),
        trace_id=uuid4(),
        status="RUNNING",
        dataset_version="dataset-v1",
        dataset_artifact_ref="gs://bucket/datasets/dataset-v1/train.jsonl",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
    )


def _make_service(
    *,
    state_store: _RunStateStore,
    training_runner=None,
    evaluator=None,
    evaluation_recorder=None,
    handoff_sink=None,
) -> RunFlowService:
    return RunFlowService(
        run_state_store=state_store,
        training_runner=training_runner or _FakeTrainingRunner(),
        evaluator=evaluator or _FakeEvaluator("PASS"),
        evaluation_recorder=evaluation_recorder or _EvaluationRecorder(),
        handoff_sink=handoff_sink or _HandoffSink(),
        evaluation_dataset_ref="gs://bucket/eval/eval-v1.json",
        training_config_ref="configs/training/smoke.yaml",
        training_config_hash="sha256:abc123",
        clock=_FakeClock(),
    )


class TestRunFlowSuccess:
    async def test_marks_ready_for_release_and_calls_handoff(self, tmp_path: Path) -> None:
        run = _make_run()
        state_store = _RunStateStore([run])
        recorder = _EvaluationRecorder()
        handoff = _HandoffSink()

        await _make_service(
            state_store=state_store,
            evaluator=_FakeEvaluator("PASS"),
            evaluation_recorder=recorder,
            handoff_sink=handoff,
        ).run(run.id, workspace_dir=tmp_path)

        assert state_store.runs[run.id].status == "READY_FOR_RELEASE"
        assert recorder.saved_results[0].overall_decision == "PASS"
        assert handoff.calls == [(run.id, run.trace_id)]


class TestRunFlowTrainingStage:
    async def test_training_error_marks_failed_without_evaluation(self, tmp_path: Path) -> None:
        run = _make_run()
        state_store = _RunStateStore([run])
        recorder = _EvaluationRecorder()
        handoff = _HandoffSink()

        await _make_service(
            state_store=state_store,
            training_runner=_FailingTrainingRunner(),
            evaluation_recorder=recorder,
            handoff_sink=handoff,
        ).run(run.id, workspace_dir=tmp_path)

        failed = state_store.runs[run.id]
        assert failed.status == "FAILED"
        assert failed.failed_stage == "학습"
        assert failed.failure_type == "ERROR"
        assert "training crashed" in failed.failure_reason
        assert recorder.saved_results == []
        assert handoff.calls == []


class TestRunFlowEvaluationStage:
    async def test_evaluation_error_marks_failed_without_handoff(self, tmp_path: Path) -> None:
        run = _make_run()
        state_store = _RunStateStore([run])
        handoff = _HandoffSink()

        await _make_service(
            state_store=state_store,
            evaluator=_FailingEvaluator(),
            handoff_sink=handoff,
        ).run(run.id, workspace_dir=tmp_path)

        failed = state_store.runs[run.id]
        assert failed.status == "FAILED"
        assert failed.failed_stage == "평가"
        assert failed.failure_type == "ERROR"
        assert "evaluation crashed" in failed.failure_reason
        assert handoff.calls == []

    async def test_evaluation_recorder_error_marks_failed_without_handoff(self, tmp_path: Path) -> None:
        run = _make_run()
        state_store = _RunStateStore([run])
        handoff = _HandoffSink()

        await _make_service(
            state_store=state_store,
            evaluation_recorder=_FailingEvaluationRecorder(),
            handoff_sink=handoff,
        ).run(run.id, workspace_dir=tmp_path)

        failed = state_store.runs[run.id]
        assert failed.status == "FAILED"
        assert failed.failed_stage == "평가"
        assert failed.failure_type == "ERROR"
        assert "evaluation_result_save_failed" in failed.failure_reason
        assert "evaluation storage crashed" in failed.failure_reason
        assert handoff.calls == []

    async def test_evaluation_fail_decision_marks_failed_without_handoff(self, tmp_path: Path) -> None:
        run = _make_run()
        state_store = _RunStateStore([run])
        recorder = _EvaluationRecorder()
        handoff = _HandoffSink()

        await _make_service(
            state_store=state_store,
            evaluator=_FakeEvaluator("FAIL"),
            evaluation_recorder=recorder,
            handoff_sink=handoff,
        ).run(run.id, workspace_dir=tmp_path)

        failed = state_store.runs[run.id]
        assert failed.status == "FAILED"
        assert failed.failed_stage == "평가"
        assert failed.failure_type == "FAIL"
        assert handoff.calls == []


class TestRunFlowHandoff:
    async def test_handoff_failure_keeps_ready_for_release_state(self, tmp_path: Path) -> None:
        run = _make_run()
        state_store = _RunStateStore([run])
        recorder = _EvaluationRecorder()
        handoff = _FailingHandoffSink()

        await _make_service(
            state_store=state_store,
            evaluator=_FakeEvaluator("PASS"),
            evaluation_recorder=recorder,
            handoff_sink=handoff,
        ).run(run.id, workspace_dir=tmp_path)

        ready = state_store.runs[run.id]
        assert ready.status == "READY_FOR_RELEASE"
        assert recorder.saved_results[0].overall_decision == "PASS"
        assert handoff.calls == [(run.id, run.trace_id)]
