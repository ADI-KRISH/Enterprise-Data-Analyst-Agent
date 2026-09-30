"""agents/executor.py against the live database (skipped when Postgres is unreachable)."""

import json

import pytest
from pydantic import SecretStr

from agents.executor import GENERIC_ERROR, QueryExecutor, UnsafeConnectionError


def test_revenue_by_region(executor, audit_path):
    r = executor.run("""
        SELECT r.region_name, SUM(oi.line_revenue) AS revenue, COUNT(DISTINCT o.order_id) AS orders
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        JOIN dim_store s ON s.store_id = o.store_id
        JOIN dim_region r ON r.region_id = s.region_id
        WHERE o.status = 'Completed'
        GROUP BY r.region_name ORDER BY revenue DESC""")
    assert r.ok, r.error_code
    assert r.columns == ["region_name", "revenue", "orders"]
    assert r.row_count == 5 and len(r.records) == 5 and not r.truncated
    assert {rec["region_name"] for rec in r.records} == {
        "North India", "South India", "East India", "West India", "Central India"}
    first = r.records[0]
    assert isinstance(first["revenue"], float) and isinstance(first["orders"], int)
    assert r.elapsed_ms > 0 and 0 < r.cost <= executor.max_cost
    assert "LIMIT 1000" in r.sql
    json.dumps(r.records)  # JSON-safe


def test_json_safe_types(executor, audit_path):
    r = executor.run("""
        SELECT o.order_date, date_trunc('month', o.order_date) AS m, oi.discount_pct,
               oi.line_revenue, o.channel, NULL AS nothing
        FROM fact_orders o JOIN fact_order_items oi ON oi.order_id = o.order_id
        ORDER BY o.order_id LIMIT 3""")
    assert r.ok
    rec = r.records[0]
    assert rec["order_date"] == "2024-10-01"
    assert isinstance(rec["m"], str) and rec["m"].startswith("2024-10-01")
    assert isinstance(rec["discount_pct"], float) and isinstance(rec["line_revenue"], float)
    assert rec["nothing"] is None
    json.dumps(r.public_dict())


def test_duplicate_column_names_are_disambiguated(executor, audit_path):
    r = executor.run("SELECT COUNT(*), COUNT(*) FROM dim_region")
    assert r.ok and len(set(r.columns)) == 2
    assert all(len(rec) == 2 for rec in r.records)


def test_like_percent_is_not_a_placeholder(executor, audit_path):
    r = executor.run("SELECT COUNT(*) AS n FROM dim_store WHERE store_name LIKE 'Chennai%'")
    assert r.ok and r.records == [{"n": 2}]


def test_colon_in_string_literal(executor, audit_path):
    r = executor.run("SELECT 'a:b' AS s, region_id::text AS t FROM dim_region LIMIT 1")
    assert r.ok and r.records[0]["s"] == "a:b"


def test_default_limit_and_truncation_flag(ro_conninfo, audit_path):
    ex = QueryExecutor(max_rows=10)
    try:
        r = ex.run("SELECT order_id FROM fact_orders ORDER BY order_id")
        assert r.ok and r.row_count == 10 and r.truncated
        assert r.records[0] == {"order_id": 1}
    finally:
        ex.dispose()


def test_empty_result(executor, audit_path):
    r = executor.run("SELECT order_id FROM fact_orders WHERE order_date < DATE '2000-01-01'")
    assert r.ok and r.row_count == 0 and r.records == [] and r.columns == ["order_id"]


def test_session_is_read_only_with_timeout(executor):
    settings = executor.session_settings()
    assert settings == {
        "current_user": "analyst_ro",
        "search_path": "analytics",
        "transaction_read_only": "on",
        "statement_timeout": "15s",
    }


def test_statement_timeout_reports_generic_error(ro_conninfo, audit_path):
    ex = QueryExecutor(timeout_ms=1)
    try:
        r = ex.run("""SELECT p.category, SUM(oi.line_revenue) FROM fact_order_items oi
                      JOIN dim_product p ON p.product_id = oi.product_id GROUP BY p.category""")
        assert not r.ok and r.error_code == "timeout" and r.error == GENERIC_ERROR
    finally:
        ex.dispose()


def test_request_id_propagates(executor, audit_path):
    r = executor.run("SELECT region_name FROM dim_region", request_id="req-123")
    assert r.request_id == "req-123"
    log = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert any(e["request_id"] == "req-123" and e["event"] == "sql_executed" for e in log)


def test_refuses_non_readonly_role():
    with pytest.raises(UnsafeConnectionError):
        QueryExecutor(url=SecretStr("postgresql+psycopg://admin:pw@localhost:5432/analytics"))


def test_public_dict_has_no_internal_fields(executor, audit_path):
    r = executor.run("SELECT 1; SELECT 2")
    public = r.public_dict()
    assert "validation_errors" not in public and public["error"] == GENERIC_ERROR
