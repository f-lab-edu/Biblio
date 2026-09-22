"""검색 한 번의 단계별 시간을 기록한다.

  `SearchOrchestrator.execute()` 한 번이 실행되는 동안 각 단계의 경과 시간을 모으고,
  마지막에 `search.execute.timing` 로그를 정확히 한 건 남긴다.
  `measure()`는 감싼 코드에서 발생한 예외를 기록한 뒤 그대로 다시 발생시키므로, 검색 결과나 기존 오류 처리에는 영향을 주지 않는다.
"""

from collections.abc import Generator
from contextlib import contextmanager
from time import perf_counter
from typing import Literal

from src.common.logging import info as log_info
from src.common.observability import SearchRequestContext, elapsed_ms
from src.middlewares.error_handler import ApiError

EXECUTE_TIMING_LOG = "search.execute.timing"

TimingStage = Literal[
    "query_embedding",
    "query_embedding_active",
    "query_embedding_previous",
    "fts",
    "vector_search",
    "vector_search_active",
    "vector_search_previous",
    "sot_gate",
    "prompt_build",
    "llm",
    "snapshot_save",
]

SearchStatus = Literal["success", "empty", "failed"]

QUERY_EMBEDDING_STAGES: dict[str, TimingStage] = {
    "active": "query_embedding_active",
    "previous": "query_embedding_previous",
}

VECTOR_SEARCH_STAGES: dict[str, TimingStage] = {
    "active": "vector_search_active",
    "previous": "vector_search_previous",
}


def _error_fields(error: BaseException) -> dict[str, str]:
    """Stable error identity only. Exception messages are never logged."""
    if isinstance(error, ApiError):
        return {"error_code": error.code}
    return {"error_type": type(error).__name__} # 예외가 들어왔을 때 해당 에러 클래스의 이름만 반환


class SearchTimingRecorder:
    def __init__(self, context: SearchRequestContext) -> None:
        self._context = context
        self._started_at = perf_counter() # 전체 시작 시간
        self._stage_ms: dict[str, float] = {} #  # 단계별 시간 저장 목적
        self._failed_stage: str | None = None
        self._target_count: int | None = None # # active/previous 몇 개 쓰는지
        self._logged = False

    @contextmanager
    def measure(self, stage: TimingStage) -> Generator[None, None, None]:
        """Record elapsed time for `stage`, including on failure or cancel."""
        stage_started_at = perf_counter()
        try:
            yield
        except BaseException:
            self._mark_failed_stage(stage)
            raise
        finally:
            self._stage_ms[f"{stage}_ms"] = elapsed_ms(stage_started_at)

    def set_target_count(self, target_count: int) -> None:
        self._target_count = target_count

    def log_success(self) -> None:
        self._log("success")

    def log_empty(self) -> None:
        self._log("empty")

    def log_failure(self, error: BaseException) -> None:
        self._log("failed", error)

    def _mark_failed_stage(self, stage: TimingStage) -> None:
        """Keep the innermost stage that failed first."""
        if self._failed_stage is None:
            self._failed_stage = stage

    def _log(self, status: SearchStatus, error: BaseException | None = None) -> None:
        if self._logged:
            return
        self._logged = True
        log_info(EXECUTE_TIMING_LOG, **self._log_fields(status, error))

    def _log_fields(
        self,
        status: SearchStatus,
        error: BaseException | None,
    ) -> dict[str, object]:
        fields: dict[str, object] = {
            **self._context.as_log_fields(),
            "status": status,
        }
        if error is not None:
            fields.update(_error_fields(error))
        if self._failed_stage is not None:
            fields["failed_stage"] = self._failed_stage
        if self._target_count is not None:
            fields["target_count"] = self._target_count
        fields.update(self._stage_ms)
        fields["total_ms"] = elapsed_ms(self._started_at)
        return fields
