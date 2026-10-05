"""Integration tests: need the seeded Postgres (skipped when unreachable)."""

import pytest
from sqlalchemy import text

from agents.executor import df_to_frame, execute_sql, frame_to_df, get_anchor_date, get_engine
from agents.sql_validator import validate_sql


@pytest.fixture(scope="module")
def engine():
    try:
        eng = get_engine()
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("database not reachable")
    return eng


def test_runs_validated_query(engine):
    v = validate_sql("SELECT region_name FROM dim_region ORDER BY 1")
    r = execute_sql(v.sql, engine)
    assert r.ok and len(r.df) == 5


def test_write_blocked_by_database_even_if_validator_bypassed(engine):
    r = execute_sql("DELETE FROM analytics.dim_region", engine)
    assert not r.ok


def test_timeout_setting(engine):
    with engine.connect() as c:
        assert c.execute(text("SHOW statement_timeout")).scalar() == "15s"
        assert c.execute(text("SHOW default_transaction_read_only")).scalar() == "on"


def test_bad_column_reports_error(engine):
    r = execute_sql("SELECT nope FROM analytics.dim_region", engine)
    assert not r.ok and r.stage == "explain"


def test_anchor_date(engine):
    assert get_anchor_date(engine).year >= 2025


def test_frame_roundtrip(engine):
    r = execute_sql("SELECT order_date, SUM(line_revenue) AS rev FROM analytics.fact_orders "
                    "JOIN analytics.fact_order_items USING (order_id) GROUP BY 1 LIMIT 3", engine)
    frame = df_to_frame(r.df)
    assert isinstance(frame["records"][0]["rev"], float)
    assert isinstance(frame["records"][0]["order_date"], str)
    assert list(frame_to_df(frame).columns) == ["order_date", "rev"]
