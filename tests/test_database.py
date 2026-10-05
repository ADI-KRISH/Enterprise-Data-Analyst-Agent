"""Read-only guarantees at the database level (need the seeded database)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agents.executor import ExecutionError, get_anchor_date, get_engine, run_query


def test_anchor_date_is_max_order_date_not_system_clock(db):
    import datetime as dt

    assert get_anchor_date() == dt.date(2026, 9, 30)


@pytest.mark.parametrize("stmt", [
    "DELETE FROM fact_orders", "UPDATE dim_region SET zone = 'x'", "DROP TABLE dim_region",
    "CREATE TABLE evil (a int)", "INSERT INTO dim_region VALUES (99, 'x', 'y')",
])
def test_ro_role_cannot_write_even_bypassing_the_validator(db, stmt):
    with pytest.raises(DBAPIError):
        with get_engine().begin() as conn:
            conn.execute(text(stmt))


def test_session_is_read_only_with_timeout(db):
    with get_engine().connect() as conn:
        assert conn.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
        assert conn.execute(text("SHOW statement_timeout")).scalar() == "15s"


def test_slow_query_is_cancelled(db):
    with pytest.raises(ExecutionError, match="timeout"):
        run_query("SELECT count(*) FROM generate_series(1, 100000000000) g")


def test_explain_dry_run_catches_errors_before_execution(db):
    with pytest.raises(ExecutionError, match="does not exist"):
        run_query("SELECT nope FROM fact_orders")
