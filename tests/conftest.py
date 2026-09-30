"""Shared fixtures. Live-DB tests are skipped when Postgres is not reachable."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def ro_conninfo() -> str:
    """libpq URL for analyst_ro; skips the test if the database is unreachable."""
    import psycopg

    from app.config import get_settings
    from database.seed import to_conninfo

    url = to_conninfo(get_settings().database_url_ro.get_secret_value())
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {type(e).__name__}")
    return url


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    """Redirect the audit log to a temp file and return its path."""
    from app.config import get_settings

    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(get_settings(), "audit_log_path", path)
    return path


@pytest.fixture(scope="session")
def executor(ro_conninfo):
    from agents.executor import QueryExecutor

    ex = QueryExecutor()
    yield ex
    ex.dispose()
