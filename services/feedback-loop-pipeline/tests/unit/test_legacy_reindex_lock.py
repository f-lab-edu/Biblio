from __future__ import annotations

from dataclasses import dataclass

from src.infra.db.legacy_reindex_lock import PostgresAdvisoryLegacyReindexLock


@dataclass(frozen=True)
class _Dialect:
    name: str


@dataclass(frozen=True)
class _Bind:
    dialect: _Dialect


class _Result:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _Session:
    def __init__(self, *, dialect_name: str, lock_value: bool = True) -> None:
        self._bind = _Bind(dialect=_Dialect(name=dialect_name))
        self._lock_value = lock_value
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_bind(self) -> _Bind:
        return self._bind

    async def execute(self, statement: object, params: dict[str, str]) -> _Result:
        self.calls.append((str(statement), params))
        return _Result(self._lock_value)


async def test_postgres_lock_uses_advisory_lock_and_unlock() -> None:
    session = _Session(dialect_name="postgresql", lock_value=True)
    lock = PostgresAdvisoryLegacyReindexLock(session, lock_name="legacy-reindex-test")

    acquired = await lock.try_acquire()
    await lock.release()

    assert acquired is True
    assert len(session.calls) == 2
    (lock_statement, lock_params), (unlock_statement, unlock_params) = session.calls
    assert "pg_try_advisory_lock" in lock_statement
    assert "pg_advisory_unlock" in unlock_statement
    assert lock_params == {"lock_name": "legacy-reindex-test"}
    assert unlock_params == {"lock_name": "legacy-reindex-test"}


async def test_non_postgres_lock_is_local_noop() -> None:
    session = _Session(dialect_name="sqlite")
    lock = PostgresAdvisoryLegacyReindexLock(session)

    acquired = await lock.try_acquire()
    await lock.release()

    assert acquired is True
    assert session.calls == []
