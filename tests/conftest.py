import pytest
from sqlalchemy import text


def _db_up() -> bool:
    try:
        from agents.executor import get_engine

        with get_engine().connect() as conn:
            return conn.execute(text("SELECT count(*) FROM fact_orders")).scalar() > 0
    except Exception:
        return False


@pytest.fixture(scope="session")
def db():
    """Skip DB-backed tests when Postgres is not up and seeded."""
    if not _db_up():
        pytest.skip("PostgreSQL not available/seeded (docker compose up -d db; python -m database.seed)")
