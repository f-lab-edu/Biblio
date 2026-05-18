from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping

from src.dataset.manifest import (
    GENERATION_RULE_VERSION,
    DatasetEligibilityPolicy,
    DatasetManifest,
)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, list | tuple):
        return ()
    return tuple(str(value) for value in values)


@dataclass(frozen=True)
class RawFeedbackEvent:
    event_id: str
    trace_id: str
    req_id: str
    user_id: str
    project_id: str
    query_text: str
    rating: str
    topk_ids: tuple[str, ...]
    used_ids: tuple[str, ...]
    active_model_version: str
    active_index_name: str
    response_snapshot_ref: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "topk_ids", tuple(self.topk_ids))
        object.__setattr__(self, "used_ids", tuple(self.used_ids))

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
            topk_ids=_string_tuple(data["topk_ids"]),
            used_ids=_string_tuple(data["used_ids"]),
            active_model_version=str(data["active_model_version"]),
            active_index_name=str(data["active_index_name"]),
            response_snapshot_ref=str(data["response_snapshot_ref"]),
            created_at=_parse_datetime(data["created_at"]),
        )


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    text: str
    source: str
    confidence: float


@dataclass(frozen=True)
class RetrievalTrainingGroup:
    query_text: str
    positives: tuple[RetrievalCandidate, ...]
    negatives: tuple[RetrievalCandidate, ...]
    source_event_ids: tuple[str, ...]
    project_id: str
    rating: str
    trace_id: str
    req_id: str
    user_id: str
    feedback_created_at: datetime
    response_snapshot_ref: str
    topk_ids: tuple[str, ...]
    used_ids: tuple[str, ...]
    source_active_model_version: str
    source_active_index_name: str
    generation_rule_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "positives", tuple(self.positives))
        object.__setattr__(self, "negatives", tuple(self.negatives))
        object.__setattr__(self, "source_event_ids", tuple(self.source_event_ids))
        object.__setattr__(self, "topk_ids", tuple(self.topk_ids))
        object.__setattr__(self, "used_ids", tuple(self.used_ids))

    def to_dict(self) -> dict[str, object]:
        return {
            "query_text": self.query_text,
            "positives": [asdict(candidate) for candidate in self.positives],
            "negatives": [asdict(candidate) for candidate in self.negatives],
            "source_event_ids": list(self.source_event_ids),
            "project_id": self.project_id,
            "rating": self.rating,
            "trace_id": self.trace_id,
            "req_id": self.req_id,
            "user_id": self.user_id,
            "feedback_created_at": self.feedback_created_at.isoformat(),
            "response_snapshot_ref": self.response_snapshot_ref,
            "topk_ids": list(self.topk_ids),
            "used_ids": list(self.used_ids),
            "source_active_model_version": self.source_active_model_version,
            "source_active_index_name": self.source_active_index_name,
            "generation_rule_version": self.generation_rule_version,
        }


@dataclass(frozen=True)
class MaterializedDataset:
    manifest: DatasetManifest
    rows: tuple[RetrievalTrainingGroup, ...]
    eligible: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))


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
        random_negative_pool_by_project_id: Mapping[str, Mapping[str, str]] | None = None,
        dataset_version: str,
        created_at: datetime,
        source_window_start: datetime,
        source_window_end: datetime,
    ) -> MaterializedDataset:
        parsed_events = [self._coerce_event(event) for event in events]
        deduped_events = self._dedupe_latest_by_event_id(parsed_events)
        rows: list[RetrievalTrainingGroup] = []
        trainable_event_count = 0
        positive_count = 0
        negative_count = 0
        negative_source_counts: dict[str, int] = {}
        missing_text_drop_count = 0
        random_negative_pool_by_project_id = random_negative_pool_by_project_id or {}
        for event in sorted(deduped_events, key=lambda item: (item.created_at, item.event_id)):
            event_group, event_missing_text_drop_count = self._group_for_event(
                event,
                chunk_text_by_id,
                random_negative_pool_by_project_id.get(event.project_id, {}),
            )
            missing_text_drop_count += event_missing_text_drop_count
            if event_group is not None:
                trainable_event_count += 1
                positive_count += len(event_group.positives)
                negative_count += len(event_group.negatives)
                for negative in event_group.negatives:
                    negative_source_counts[negative.source] = negative_source_counts.get(negative.source, 0) + 1
                rows.append(event_group)

        manifest = DatasetManifest(
            dataset_version=dataset_version,
            created_at=created_at,
            generation_rule_version=self._generation_rule_version,
            source_window_start=source_window_start,
            source_window_end=source_window_end,
            input_event_count=len(parsed_events),
            deduped_event_count=len(deduped_events),
            trainable_event_count=trainable_event_count,
            training_group_count=len(rows),
            positive_count=positive_count,
            negative_count=negative_count,
            negative_source_counts=negative_source_counts,
            missing_text_drop_count=missing_text_drop_count,
        ).with_eligibility(self._eligibility_policy)
        return MaterializedDataset(
            manifest=manifest,
            rows=tuple(rows),
            eligible=manifest.eligible,
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

    def _group_for_event(
        self,
        event: RawFeedbackEvent,
        chunk_text_by_id: Mapping[str, str],
        same_project_chunk_text_by_id: Mapping[str, str],
    ) -> tuple[RetrievalTrainingGroup | None, int]:
        if event.rating != self._positive_rating:
            return None, 0
        positives: list[RetrievalCandidate] = []
        negatives: list[RetrievalCandidate] = []
        missing_text_drop_count = 0
        used_id_set = set(event.used_ids)
        for positive_chunk_id in event.used_ids:
            positive_text = chunk_text_by_id.get(positive_chunk_id)
            if positive_text is None:
                missing_text_drop_count += 1
                continue
            positives.append(
                RetrievalCandidate(
                    chunk_id=positive_chunk_id,
                    text=positive_text,
                    source="liked_response_used_chunk",
                    confidence=0.8,
                )
            )

        exposed_unused_ids = [chunk_id for chunk_id in event.topk_ids if chunk_id not in used_id_set]
        for negative_chunk_id in exposed_unused_ids:
            negative_text = chunk_text_by_id.get(negative_chunk_id)
            if negative_text is None:
                missing_text_drop_count += 1
                continue
            negatives.append(
                RetrievalCandidate(
                    chunk_id=negative_chunk_id,
                    text=negative_text,
                    source="exposed_unused",
                    confidence=0.4,
                )
            )
        negatives.extend(
            self._same_project_random_negatives(
                event=event,
                same_project_chunk_text_by_id=same_project_chunk_text_by_id,
                excluded_chunk_ids=set(event.used_ids).union(exposed_unused_ids),
                exposed_unused_count=len(negatives),
            )
        )

        if not positives or not negatives:
            return None, missing_text_drop_count

        return (
            RetrievalTrainingGroup(
                query_text=event.query_text,
                positives=positives,
                negatives=negatives,
                source_event_ids=(event.event_id,),
                project_id=event.project_id,
                rating=event.rating,
                trace_id=event.trace_id,
                req_id=event.req_id,
                user_id=event.user_id,
                feedback_created_at=event.created_at,
                response_snapshot_ref=event.response_snapshot_ref,
                topk_ids=tuple(event.topk_ids),
                used_ids=tuple(event.used_ids),
                source_active_model_version=event.active_model_version,
                source_active_index_name=event.active_index_name,
                generation_rule_version=self._generation_rule_version,
            ),
            missing_text_drop_count,
        )

    @staticmethod
    def _same_project_random_negatives(
        *,
        event: RawFeedbackEvent,
        same_project_chunk_text_by_id: Mapping[str, str],
        excluded_chunk_ids: set[str],
        exposed_unused_count: int,
    ) -> list[RetrievalCandidate]:
        candidate_ids = [
            chunk_id
            for chunk_id in same_project_chunk_text_by_id
            if chunk_id not in excluded_chunk_ids
        ]
        if not candidate_ids:
            return []
        target_count = min(3, max(1, math.ceil(exposed_unused_count * 0.5)))
        selected_ids = sorted(
            candidate_ids,
            key=lambda chunk_id: _stable_random_key(event.event_id, chunk_id),
        )[:target_count]
        return [
            RetrievalCandidate(
                chunk_id=chunk_id,
                text=same_project_chunk_text_by_id[chunk_id],
                source="random_same_project",
                confidence=0.2,
            )
            for chunk_id in selected_ids
        ]


def _stable_random_key(event_id: str, chunk_id: str) -> str:
    return hashlib.sha256(f"{event_id}\0{chunk_id}".encode()).hexdigest()
