from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping

from src.dataset.manifest import (
    GENERATION_RULE_VERSION,
    DatasetEligibilityPolicy,
    DatasetManifest,
    is_manifest_eligible,
)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values]


@dataclass(frozen=True)
class RawFeedbackEvent:
    event_id: str
    trace_id: str
    req_id: str
    user_id: str
    project_id: str
    query_text: str
    rating: str
    topk_ids: list[str]
    used_ids: list[str]
    active_model_version: str
    active_index_name: str
    response_snapshot_ref: str
    created_at: datetime

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> RawFeedbackEvent:
        return cls(
            event_id=str(data["event_id"]),
            trace_id=str(data["trace_id"]),
            req_id=str(data["req_id"]),
            user_id=str(data["user_id"]),
            project_id=str(data["project_id"]),
            query_text=str(data["query_text"]),
            rating=str(data["rating"]).upper(),
            topk_ids=_string_list(data["topk_ids"]),
            used_ids=_string_list(data["used_ids"]),
            active_model_version=str(data["active_model_version"]),
            active_index_name=str(data["active_index_name"]),
            response_snapshot_ref=str(data["response_snapshot_ref"]),
            created_at=_parse_datetime(data["created_at"]),
        )


@dataclass(frozen=True)
class TrainingTripletRow:
    event_id: str
    trace_id: str
    req_id: str
    user_id: str
    query_text: str
    project_id: str
    rating: str
    feedback_created_at: datetime
    response_snapshot_ref: str
    topk_ids: list[str]
    used_ids: list[str]
    positive_chunk_id: str
    positive_text: str
    hard_negative_chunk_id: str
    hard_negative_text: str
    source_active_model_version: str
    source_active_index_name: str
    label_source: str
    generation_rule_version: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["feedback_created_at"] = self.feedback_created_at.isoformat()
        return data


@dataclass(frozen=True)
class MaterializedDataset:
    manifest: DatasetManifest
    rows: list[TrainingTripletRow]
    eligible: bool


class DatasetMaterializer:
    def __init__(
        self,
        *,
        generation_rule_version: str = GENERATION_RULE_VERSION,
        positive_rating: str = "LIKE",
        eligibility_policy: DatasetEligibilityPolicy | None = None,
    ) -> None:
        self._generation_rule_version = generation_rule_version
        self._positive_rating = positive_rating
        self._eligibility_policy = eligibility_policy or DatasetEligibilityPolicy()

    def materialize(
        self,
        events: list[Mapping[str, object] | RawFeedbackEvent],
        *,
        chunk_text_by_id: Mapping[str, str],
        dataset_version: str,
        created_at: datetime,
        source_window_start: datetime,
        source_window_end: datetime,
    ) -> MaterializedDataset:
        parsed_events = [self._coerce_event(event) for event in events]
        deduped_events = self._dedupe_latest_by_event_id(parsed_events)
        rows: list[TrainingTripletRow] = []
        trainable_event_count = 0
        for event in sorted(deduped_events, key=lambda item: (item.created_at, item.event_id)):
            event_rows = self._rows_for_event(event, chunk_text_by_id)
            if event_rows:
                trainable_event_count += 1
                rows.extend(event_rows)

        manifest = DatasetManifest(
            dataset_version=dataset_version,
            created_at=created_at,
            generation_rule_version=self._generation_rule_version,
            source_window_start=source_window_start,
            source_window_end=source_window_end,
            input_event_count=len(parsed_events),
            deduped_event_count=len(deduped_events),
            trainable_event_count=trainable_event_count,
            triplet_row_count=len(rows),
        )
        return MaterializedDataset(
            manifest=manifest,
            rows=rows,
            eligible=is_manifest_eligible(manifest, self._eligibility_policy),
        )

    @staticmethod
    def _coerce_event(event: Mapping[str, object] | RawFeedbackEvent) -> RawFeedbackEvent:
        if isinstance(event, RawFeedbackEvent):
            return event
        return RawFeedbackEvent.from_mapping(event)

    @staticmethod
    def _dedupe_latest_by_event_id(events: list[RawFeedbackEvent]) -> list[RawFeedbackEvent]:
        deduped: dict[str, RawFeedbackEvent] = {}
        for event in events:
            previous = deduped.get(event.event_id)
            if previous is None or event.created_at >= previous.created_at:
                deduped[event.event_id] = event
        return list(deduped.values())

    def _rows_for_event(
        self,
        event: RawFeedbackEvent,
        chunk_text_by_id: Mapping[str, str],
    ) -> list[TrainingTripletRow]:
        if event.rating != self._positive_rating:
            return []
        positive_ids = event.used_ids
        used_id_set = set(event.used_ids)
        hard_negative_ids = [chunk_id for chunk_id in event.topk_ids if chunk_id not in used_id_set]
        rows: list[TrainingTripletRow] = []
        for positive_chunk_id in positive_ids:
            positive_text = chunk_text_by_id.get(positive_chunk_id)
            if positive_text is None:
                continue
            for hard_negative_chunk_id in hard_negative_ids:
                hard_negative_text = chunk_text_by_id.get(hard_negative_chunk_id)
                if hard_negative_text is None:
                    continue
                rows.append(
                    TrainingTripletRow(
                        event_id=event.event_id,
                        trace_id=event.trace_id,
                        req_id=event.req_id,
                        user_id=event.user_id,
                        query_text=event.query_text,
                        project_id=event.project_id,
                        rating=event.rating,
                        feedback_created_at=event.created_at,
                        response_snapshot_ref=event.response_snapshot_ref,
                        topk_ids=event.topk_ids,
                        used_ids=event.used_ids,
                        positive_chunk_id=positive_chunk_id,
                        positive_text=positive_text,
                        hard_negative_chunk_id=hard_negative_chunk_id,
                        hard_negative_text=hard_negative_text,
                        source_active_model_version=event.active_model_version,
                        source_active_index_name=event.active_index_name,
                        label_source="feedback_like",
                        generation_rule_version=self._generation_rule_version,
                    )
                )
        return rows
