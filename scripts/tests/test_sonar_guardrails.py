from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_run_search_queries_binds_each_query_value_in_inner_search_function() -> None:
    shared_path = ROOT / "scripts" / "e2e_backend_smoke_shared.py"
    content = shared_path.read_text(encoding="utf-8")

    assert "def _search(current_query: str = query) -> dict[str, Any]:" in content
    assert 'json_body={"query": current_query}' in content


def test_e2e_backend_smoke_test_avoids_passwordless_database_url_literal() -> None:
    test_path = ROOT / "scripts" / "tests" / "test_e2e_backend_smoke.py"
    content = test_path.read_text(encoding="utf-8")

    assert '"postgresql+asyncpg://alice@localhost:55433/sample"' not in content
    assert "SplitResult(" in content
