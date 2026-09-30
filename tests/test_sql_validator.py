"""Normal (should pass) cases for agents/sql_validator.py. Attacks live in test_security.py."""

import re
from pathlib import Path

import pytest
import sqlglot

from agents.sql_validator import validate

KPI_DOC = Path(__file__).resolve().parent.parent / "retrieval" / "docs" / "kpi_definitions.md"
KPI_QUERIES = [q.strip() for q in re.findall(r"```sql\n(.*?)```", KPI_DOC.read_text(), re.S)]


def ok(sql: str) -> str:
    r = validate(sql)
    assert r.ok, r.errors
    assert r.errors == []
    return r.normalized_sql


def limit_of(sql: str) -> int | None:
    tree = sqlglot.parse_one(sql, read="postgres")
    lim = tree.args.get("limit")
    return int(lim.expression.name) if lim else None


def test_kpi_doc_has_reference_queries():
    assert len(KPI_QUERIES) >= 9


@pytest.mark.parametrize("sql", KPI_QUERIES, ids=[f"kpi_ref_{i}" for i in range(len(KPI_QUERIES))])
def test_kpi_reference_queries_pass(sql):
    ok(sql)


NORMAL = {
    "simple_select": "SELECT region_name FROM dim_region",
    "qualified_schema": "SELECT r.region_name FROM analytics.dim_region r",
    "uppercase": "SELECT REGION_NAME, ZONE FROM DIM_REGION WHERE ZONE = 'South'",
    "trailing_semicolon": "SELECT store_name FROM dim_store;",
    "comment_stripped": "-- revenue by channel\nSELECT channel, COUNT(*) AS n FROM fact_orders GROUP BY channel",
    "cte_aliases": """
        WITH rev AS (
            SELECT o.store_id AS sid, SUM(oi.line_revenue) AS revenue
            FROM fact_order_items oi JOIN fact_orders o ON o.order_id = oi.order_id
            WHERE o.status = 'Completed' GROUP BY o.store_id)
        SELECT s.store_name, rev.revenue FROM rev JOIN dim_store s ON s.store_id = rev.sid
        ORDER BY rev.revenue DESC LIMIT 5""",
    "two_ctes": """
        WITH a AS (SELECT store_id, COUNT(*) AS n FROM fact_orders GROUP BY store_id),
             b AS (SELECT store_id, n FROM a WHERE n > 100)
        SELECT COUNT(*) FROM b""",
    "union": "SELECT city FROM dim_store UNION SELECT city FROM dim_customer",
    "subquery_in_where": """SELECT COUNT(*) FROM fact_orders
        WHERE store_id IN (SELECT store_id FROM dim_store WHERE city = 'Chennai')""",
    "exists": """SELECT s.store_name FROM dim_store s
        WHERE EXISTS (SELECT 1 FROM fact_orders o WHERE o.store_id = s.store_id AND o.channel = 'Online')""",
    "derived_table": """SELECT t.category, t.revenue FROM (
        SELECT p.category, SUM(oi.line_revenue) AS revenue FROM fact_order_items oi
        JOIN dim_product p ON p.product_id = oi.product_id GROUP BY p.category) t""",
    "having": """SELECT p.brand, SUM(oi.quantity) AS units FROM fact_order_items oi
        JOIN dim_product p ON p.product_id = oi.product_id GROUP BY p.brand HAVING SUM(oi.quantity) > 100""",
    "window_functions": """SELECT p.category, p.product_name, p.list_price,
        RANK() OVER (PARTITION BY p.category ORDER BY p.list_price DESC) AS rnk,
        ROW_NUMBER() OVER (ORDER BY p.product_id) AS rn,
        LAG(p.list_price) OVER (ORDER BY p.product_id) AS prev_price,
        SUM(p.list_price) OVER (PARTITION BY p.category) AS cat_total
        FROM dim_product p""",
    "date_functions": """SELECT date_trunc('month', o.order_date) AS m, EXTRACT(YEAR FROM o.order_date) AS y,
        date_part('quarter', o.order_date) AS q, to_char(o.order_date, 'YYYY-MM') AS ym,
        make_date(2026, 7, 1) AS d FROM fact_orders o LIMIT 10""",
    "percentile": """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY oi.line_revenue) AS median_line
        FROM fact_order_items oi""",
    "case_coalesce_cast": """SELECT CASE WHEN o.channel = 'Online' THEN 1 ELSE 0 END AS is_online,
        COALESCE(NULLIF(o.status, ''), 'n/a') AS st, CAST(o.order_id AS TEXT) AS oid,
        o.order_id::numeric AS oid_num, o.order_date >= DATE '2026-07-01' AS in_q3
        FROM fact_orders o""",
    "interval_arithmetic": """SELECT COUNT(*) FROM fact_orders
        WHERE order_date > DATE '2026-09-30' - INTERVAL '30 days'""",
    "string_functions": """SELECT lower(s.city), upper(s.state), trim(s.store_name), length(s.store_name),
        concat(s.city, ', ', s.state), substring(s.store_name, 1, 5) FROM dim_store s""",
    "math_functions": """SELECT round(avg(oi.discount_pct), 2), abs(-1), ceil(1.2), floor(1.8),
        greatest(1, 2), least(1, 2), stddev(oi.quantity), min(oi.quantity), max(oi.quantity)
        FROM fact_order_items oi""",
    "restricted_in_where": "SELECT COUNT(*) FROM dim_customer WHERE name = 'Some Name'",
    "restricted_in_count": "SELECT c.segment, COUNT(DISTINCT c.name) AS n FROM dim_customer c GROUP BY c.segment",
    "restricted_group_order_by": """SELECT COUNT(*) AS n FROM dim_customer c
        GROUP BY c.name HAVING COUNT(*) > 1 ORDER BY c.name""",
    "restricted_in_join": """SELECT COUNT(*) FROM dim_customer a JOIN dim_customer b
        ON a.name = b.name AND a.customer_id < b.customer_id""",
    "restricted_inside_derived_not_output": """SELECT COUNT(*) FROM (
        SELECT c.name FROM dim_customer c WHERE c.segment = 'SMB') t""",
    "fiscal_join_dim_date": """SELECT d.fiscal_year, COUNT(*) FROM fact_orders o
        JOIN dim_date d ON d.date = o.order_date GROUP BY d.fiscal_year""",
    "no_from": "SELECT 1 AS one",
    "like_ilike_between": """SELECT COUNT(*) FROM dim_product WHERE product_name ILIKE '%phone%'
        AND category LIKE 'E%' AND list_price BETWEEN 1000 AND 5000""",
}


@pytest.mark.parametrize("sql", NORMAL.values(), ids=NORMAL.keys())
def test_normal_queries_pass(sql):
    normalized = ok(sql)
    # normalized SQL is itself valid and stable
    again = validate(normalized)
    assert again.ok and again.normalized_sql == normalized


def test_limit_injected_when_missing():
    assert limit_of(ok("SELECT region_name FROM dim_region")) == 1000


def test_existing_small_limit_kept():
    assert limit_of(ok("SELECT region_name FROM dim_region LIMIT 3")) == 3


def test_large_limit_clamped():
    assert limit_of(ok("SELECT order_id FROM fact_orders LIMIT 10000000")) == 5000


def test_limit_all_replaced():
    assert limit_of(ok("SELECT order_id FROM fact_orders LIMIT ALL")) == 1000


def test_fetch_first_becomes_limit():
    sql = ok("SELECT order_id FROM fact_orders ORDER BY order_id FETCH FIRST 7 ROWS ONLY")
    assert limit_of(sql) == 7 and "FETCH" not in sql.upper()


def test_union_gets_limit():
    sql = ok("SELECT city FROM dim_store UNION SELECT city FROM dim_customer")
    assert limit_of(sql) == 1000


def test_comments_removed_from_normalized():
    sql = ok("SELECT /* region list */ region_name FROM dim_region -- done")
    assert "--" not in sql and "/*" not in sql


def test_single_statement_output():
    assert len(sqlglot.parse(ok("SELECT 1 AS x;"), read="postgres")) == 1
