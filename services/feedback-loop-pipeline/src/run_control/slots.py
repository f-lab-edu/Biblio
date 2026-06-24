from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass
class RunSlotRecord:
    id: UUID
    status: str
    dataset_version: str
    baseline_model_version: str
    candidate_model_version: str
    superseded_by_run_id: UUID | None = None


class RunSlotController:
    def __init__(self, records: list[RunSlotRecord] | None = None) -> None:
        self.records = list(records or [])

    def request_run(
        self,
        *,
        dataset_version: str,
        baseline_model_version: str,
        candidate_model_version: str,
    ) -> RunSlotRecord:
        existing = self._find_active_for_dataset(dataset_version)
        if existing is not None:
            return existing

        running = self._find_by_status("RUNNING")
        if running is None:
            return self._append_record(
                status="RUNNING",
                dataset_version=dataset_version,
                baseline_model_version=baseline_model_version,
                candidate_model_version=candidate_model_version,
            )

        pending = self._find_by_status("PENDING")
        if pending is not None:
            pending.status = "SUPERSEDED"

        new_pending = self._append_record(
            status="PENDING",
            dataset_version=dataset_version,
            baseline_model_version=baseline_model_version,
            candidate_model_version=candidate_model_version,
        )
        if pending is not None:
            pending.superseded_by_run_id = new_pending.id
        return new_pending

    def _find_active_for_dataset(self, dataset_version: str) -> RunSlotRecord | None:
        return next(
            (
                record
                for record in self.records
                if record.dataset_version == dataset_version and record.status in {"RUNNING", "PENDING"}
            ),
            None,
        )

    def _find_by_status(self, status: str) -> RunSlotRecord | None:
        return next((record for record in self.records if record.status == status), None)

    def _append_record(
        self,
        *,
        status: str,
        dataset_version: str,
        baseline_model_version: str,
        candidate_model_version: str,
    ) -> RunSlotRecord:
        record = RunSlotRecord(
            id=uuid4(),
            status=status,
            dataset_version=dataset_version,
            baseline_model_version=baseline_model_version,
            candidate_model_version=candidate_model_version,
        )
        self.records.append(record)
        return record
