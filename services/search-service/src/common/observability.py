"""검색 로그에 매번 같이 붙는 ID 4개(trace_id, req_id, user_id, project_id) 모음.

나중에 이 ID로 로그를 검색하면, 검색 한 건에 대한 모든 로그를 한 번에 모아볼 수 있다.
검색 결과를 결정하는 데는 쓰지 않는다.
"""

from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

_MS_PER_SECOND = 1000
_MS_DECIMALS = 1


@dataclass(frozen=True, slots=True)
class SearchRequestContext:
    trace_id: str
    req_id: UUID
    user_id: UUID
    project_id: UUID

    def as_log_fields(self) -> dict[str, str]:
        return {
            "trace_id": self.trace_id,
            "req_id": str(self.req_id),
            "user_id": str(self.user_id),
            "project_id": str(self.project_id),
        }


def elapsed_ms(started_at: float) -> float:
    """Milliseconds since a `perf_counter()` reading, rounded for logging."""
    return round((perf_counter() - started_at) * _MS_PER_SECOND, _MS_DECIMALS)
