from uuid import UUID

from src.run_control.slots import RunSlotController


def test_training_request_starts_running_when_active_slot_is_empty() -> None:
    controller = RunSlotController()

    run = controller.request_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
    )

    assert run.status == "RUNNING"
    assert run.dataset_version == "dataset-v1"
    assert isinstance(run.id, UUID)


def test_duplicate_training_request_for_running_dataset_does_not_create_parallel_run() -> None:
    controller = RunSlotController()
    first = controller.request_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
    )

    second = controller.request_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
    )

    assert second is first
    assert len(controller.records) == 1


def test_new_dataset_waits_as_single_pending_run_while_running_exists() -> None:
    controller = RunSlotController()
    controller.request_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
    )
    pending = controller.request_run(
        dataset_version="dataset-v2",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v2",
    )

    assert pending.status == "PENDING"
    assert len([record for record in controller.records if record.status == "RUNNING"]) == 1
    assert len([record for record in controller.records if record.status == "PENDING"]) == 1


def test_newer_pending_dataset_supersedes_existing_pending_run() -> None:
    controller = RunSlotController()
    controller.request_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
    )
    old_pending = controller.request_run(
        dataset_version="dataset-v2",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v2",
    )

    new_pending = controller.request_run(
        dataset_version="dataset-v3",
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v3",
    )

    assert old_pending.status == "SUPERSEDED"
    assert old_pending.superseded_by_run_id == new_pending.id
    assert new_pending.status == "PENDING"
    assert len([record for record in controller.records if record.status == "PENDING"]) == 1
