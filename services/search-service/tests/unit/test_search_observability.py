"""Unit tests for SearchTimingRecorder (stage timing + execute summary log).

Covers: elapsed time on success/failure/cancel, first-failed-stage tracking,
field contract of `search.execute.timing`, and single-log guarantee.
"""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.common.observability import SearchRequestContext, elapsed_ms
from src.middlewares.error_handler import ServiceUnavailableError
from src.services.search_observability import (
    EXECUTE_TIMING_LOG,
    SearchTimingRecorder,
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


def _capture_log(recorder_call) -> dict:
    with patch("src.services.search_observability.log_info") as log_info:
        recorder_call()
    assert log_info.call_args.args[0] == EXECUTE_TIMING_LOG
    return log_info.call_args.kwargs


class TestElapsedMs:
    def test_returns_float_rounded_to_one_decimal(self) -> None:
        with patch(
            "src.common.observability.perf_counter",
            side_effect=[1.0123456],
        ):
            value = elapsed_ms(1.0)

        assert isinstance(value, float)
        assert value == pytest.approx(12.3)

    def test_is_not_a_formatted_string(self) -> None:
        recorder = SearchTimingRecorder(_context())
        with recorder.measure("llm"):
            pass

        fields = _capture_log(recorder.log_success)
        assert isinstance(fields["llm_ms"], float)
        assert isinstance(fields["total_ms"], float)


class TestMeasure:
    def test_records_stage_on_success(self) -> None:
        recorder = SearchTimingRecorder(_context())

        with recorder.measure("prompt_build"):
            pass

        fields = _capture_log(recorder.log_success)
        assert "prompt_build_ms" in fields
        assert "failed_stage" not in fields

    def test_records_stage_and_reraises_on_exception(self) -> None:
        recorder = SearchTimingRecorder(_context())

        with pytest.raises(RuntimeError, match="boom"), recorder.measure("fts"):
            raise RuntimeError("boom")

        fields = _capture_log(recorder.log_success)
        assert "fts_ms" in fields
        assert fields["failed_stage"] == "fts"

    async def test_records_stage_and_reraises_on_cancel(self) -> None:
        recorder = SearchTimingRecorder(_context())

        async def cancelled_stage() -> None:
            with recorder.measure("llm"):
                await asyncio.sleep(10)

        task = asyncio.create_task(cancelled_stage())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        fields = _capture_log(recorder.log_success)
        assert "llm_ms" in fields
        assert fields["failed_stage"] == "llm"

    def test_keeps_innermost_failed_stage(self) -> None:
        recorder = SearchTimingRecorder(_context())

        with pytest.raises(RuntimeError), recorder.measure(
            "query_embedding"
        ), recorder.measure("query_embedding_active"):
            raise RuntimeError("embedding down")

        fields = _capture_log(recorder.log_success)
        assert fields["failed_stage"] == "query_embedding_active"
        assert "query_embedding_ms" in fields
        assert "query_embedding_active_ms" in fields

    def test_unrun_stages_are_omitted_not_zero(self) -> None:
        recorder = SearchTimingRecorder(_context())

        with recorder.measure("query_embedding_active"):
            pass

        fields = _capture_log(recorder.log_success)
        assert "query_embedding_previous_ms" not in fields
        assert "llm_ms" not in fields


class TestExecuteSummaryLog:
    def test_always_carries_correlation_and_status(self) -> None:
        recorder = SearchTimingRecorder(_context())

        fields = _capture_log(recorder.log_success)

        assert fields["trace_id"] == TRACE_ID
        assert fields["req_id"] == str(REQ_ID)
        assert fields["user_id"] == str(USER_ID)
        assert fields["project_id"] == str(PROJECT_ID)
        assert fields["status"] == "success"
        assert "total_ms" in fields

    def test_empty_status(self) -> None:
        recorder = SearchTimingRecorder(_context())

        fields = _capture_log(recorder.log_empty)

        assert fields["status"] == "empty"

    def test_known_api_failure_uses_error_code(self) -> None:
        recorder = SearchTimingRecorder(_context())
        error = ServiceUnavailableError("embedding endpoint down")

        fields = _capture_log(lambda: recorder.log_failure(error))

        assert fields["status"] == "failed"
        assert fields["error_code"] == "SERVICE_UNAVAILABLE"
        assert "error_type" not in fields

    def test_unknown_failure_uses_error_type(self) -> None:
        recorder = SearchTimingRecorder(_context())

        fields = _capture_log(lambda: recorder.log_failure(ValueError("bad")))

        assert fields["status"] == "failed"
        assert fields["error_type"] == "ValueError"
        assert "error_code" not in fields

    def test_never_logs_exception_message(self) -> None:
        recorder = SearchTimingRecorder(_context())
        error = ServiceUnavailableError("embedding endpoint down")

        fields = _capture_log(lambda: recorder.log_failure(error))

        assert "embedding endpoint down" not in str(fields)

    def test_target_count_is_omitted_until_set(self) -> None:
        recorder = SearchTimingRecorder(_context())

        fields = _capture_log(recorder.log_success)

        assert "target_count" not in fields

    def test_target_count_is_reported_when_set(self) -> None:
        recorder = SearchTimingRecorder(_context())
        recorder.set_target_count(2)

        fields = _capture_log(recorder.log_success)

        assert fields["target_count"] == 2

    def test_logs_exactly_once_per_attempt(self) -> None:
        recorder = SearchTimingRecorder(_context())

        with patch("src.services.search_observability.log_info") as log_info:
            recorder.log_success()
            recorder.log_empty()
            recorder.log_failure(ValueError("late"))

        log_info.assert_called_once()
        assert log_info.call_args.kwargs["status"] == "success"
