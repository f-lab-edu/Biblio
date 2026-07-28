"""Unit tests for the SearchRepository DB connection acquire helper.

Covers: no-op without request context, success/failure logs, exception
propagation, ANN target metadata, and the measured span boundary.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.common.observability import SearchRequestContext
from src.infra.db.search_repository import (
    DB_CONNECTION_ACQUIRE_LOG,
    _acquire_connection_for_observation,
)

TRACE_ID = str(uuid4())
REQ_ID = uuid4()
USER_ID = uuid4()
PROJECT_ID = uuid4()


def _context() -> SearchRequestContext:
    return SearchRequestContext(
        trace_id=TRACE_ID,
        req_id=REQ_ID,
        user_id=USER_ID,
        project_id=PROJECT_ID,
    )


class _FakeClock:
    """Deterministic `perf_counter` stand-in measured in seconds."""

    def __init__(self) -> None:
        self._now = 100.0

    def __call__(self) -> float:
        return self._now

    def advance(self, *, seconds: float) -> None:
        self._now += seconds

    def advancing_call(self, *, seconds: float):
        async def _call() -> None:
            self.advance(seconds=seconds)

        return _call


def _session(connection_error: Exception | None = None) -> AsyncMock:
    session = AsyncMock()
    if connection_error is not None:
        session.connection.side_effect = connection_error
    return session


class TestWithoutRequestContext:
    async def test_does_not_touch_the_connection(self) -> None:
        session = _session()

        with patch("src.infra.db.search_repository.log_info") as log_info:
            await _acquire_connection_for_observation(
                session, request_context=None, db_operation="fts"
            )

        session.connection.assert_not_awaited()
        log_info.assert_not_called()


class TestSuccess:
    async def test_acquires_connection_and_logs_info(self) -> None:
        session = _session()

        with patch("src.infra.db.search_repository.log_info") as log_info:
            await _acquire_connection_for_observation(
                session, request_context=_context(), db_operation="readiness"
            )

        session.connection.assert_awaited_once()
        assert log_info.call_args.args[0] == DB_CONNECTION_ACQUIRE_LOG
        fields = log_info.call_args.kwargs
        assert fields["trace_id"] == TRACE_ID
        assert fields["req_id"] == str(REQ_ID)
        assert fields["user_id"] == str(USER_ID)
        assert fields["project_id"] == str(PROJECT_ID)
        assert fields["status"] == "success"
        assert fields["db_operation"] == "readiness"
        assert isinstance(fields["db_connection_acquire_ms"], float)

    async def test_non_ann_operations_omit_target_metadata(self) -> None:
        session = _session()

        with patch("src.infra.db.search_repository.log_info") as log_info:
            await _acquire_connection_for_observation(
                session, request_context=_context(), db_operation="sot_gate"
            )

        fields = log_info.call_args.kwargs
        assert fields["target_role"] is None
        assert fields["model_version"] is None
        assert fields["index_name"] is None

    async def test_ann_records_role_model_version_and_index_name(self) -> None:
        session = _session()

        with patch("src.infra.db.search_repository.log_info") as log_info:
            await _acquire_connection_for_observation(
                session,
                request_context=_context(),
                db_operation="ann",
                target_role="previous",
                model_version="v002",
                index_name="candidate_v002",
            )

        fields = log_info.call_args.kwargs
        assert fields["db_operation"] == "ann"
        assert fields["target_role"] == "previous"
        assert fields["model_version"] == "v002"
        assert fields["index_name"] == "candidate_v002"

    async def test_measures_only_the_connection_call(self) -> None:
        """The span must cover session.connection() and nothing around it."""
        clock = _FakeClock()
        session = AsyncMock()
        session.connection.side_effect = clock.advancing_call(seconds=0.25)

        clock.advance(seconds=9.0)  # work before the helper must not count
        with patch("src.common.observability.perf_counter", clock), patch(
            "src.infra.db.search_repository.perf_counter", clock
        ), patch("src.infra.db.search_repository.log_info") as log_info:
            await _acquire_connection_for_observation(
                session, request_context=_context(), db_operation="fts"
            )

        assert log_info.call_args.kwargs["db_connection_acquire_ms"] == pytest.approx(
            250.0
        )


class TestFailure:
    async def test_logs_warning_and_reraises_original_exception(self) -> None:
        session = _session(TimeoutError("pool exhausted"))

        with patch(
            "src.infra.db.search_repository.log_warning"
        ) as log_warning, pytest.raises(TimeoutError, match="pool exhausted"):
            await _acquire_connection_for_observation(
                session, request_context=_context(), db_operation="snapshot"
            )

        assert log_warning.call_args.args[0] == DB_CONNECTION_ACQUIRE_LOG
        fields = log_warning.call_args.kwargs
        assert fields["status"] == "failed"
        assert fields["db_operation"] == "snapshot"
        assert fields["error_type"] == "TimeoutError"
        assert isinstance(fields["db_connection_acquire_ms"], float)

    async def test_never_logs_the_exception_message(self) -> None:
        session = _session(TimeoutError("pool exhausted"))

        with patch(
            "src.infra.db.search_repository.log_warning"
        ) as log_warning, pytest.raises(TimeoutError):
            await _acquire_connection_for_observation(
                session, request_context=_context(), db_operation="conversation"
            )

        assert "pool exhausted" not in str(log_warning.call_args.kwargs)
