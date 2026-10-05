import pytest

from agents.sql_validator import validate_sql

OK = {
    "join": "SELECT r.region_name, SUM(i.line_revenue) AS revenue FROM fact_orders o "
            "JOIN fact_order_items i ON i.order_id = o.order_id JOIN dim_customer c ON c.customer_id = o.customer_id "
            "JOIN dim_region r ON r.region_id = c.region_id WHERE o.status = 'Completed' GROUP BY r.region_name",
    "cte": "WITH t AS (SELECT order_id, status FROM analytics.fact_orders) SELECT status, COUNT(*) AS n FROM t GROUP BY status",
    "union": "SELECT region_name FROM dim_region UNION SELECT zone FROM dim_region",
    "subquery": "SELECT x.c FROM (SELECT COUNT(*) AS c FROM fact_orders) AS x",
    "filter": "SELECT SUM(line_revenue) FILTER (WHERE quantity > 1) AS r FROM fact_order_items",
    "star": "SELECT * FROM dim_store",
}

BAD = {
    "multi_statement": "SELECT 1; SELECT 2",
    "drop": "DROP TABLE fact_orders",
    "insert": "INSERT INTO dim_region VALUES (9, 'x', 'y')",
    "update": "UPDATE dim_region SET zone = 'x'",
    "delete": "DELETE FROM fact_orders",
    "truncate": "TRUNCATE fact_orders",
    "create": "CREATE TABLE t (a int)",
    "alter": "ALTER TABLE fact_orders ADD COLUMN x int",
    "grant": "GRANT ALL ON fact_orders TO PUBLIC",
    "copy": "COPY fact_orders TO '/tmp/x'",
    "call": "CALL do_something()",
    "do_block": "DO $$ BEGIN PERFORM 1; END $$",
    "select_into": "SELECT * INTO newtable FROM fact_orders",
    "for_update": "SELECT * FROM fact_orders FOR UPDATE",
    "pg_function": "SELECT pg_sleep(10)",
    "pg_read": "SELECT pg_read_file('/etc/passwd')",
    "unknown_table": "SELECT * FROM users",
    "unknown_column": "SELECT revenue FROM fact_orders",
    "unknown_alias_column": "SELECT o.nope FROM fact_orders o",
    "system_catalog": "SELECT * FROM pg_catalog.pg_tables",
    "information_schema": "SELECT * FROM information_schema.tables",
    "other_schema": "SELECT * FROM public.fact_orders",
    "comment_with_statement": "SELECT 1 FROM dim_region /* ; DROP TABLE fact_orders */",
    "line_comment_with_statement": "SELECT 1 FROM dim_region -- drop table fact_orders",
    "empty": "   ",
    "garbage": "SELEC FROM WHERE",
    "set": "SET ROLE postgres",
}


@pytest.mark.parametrize("name", OK)
def test_valid_queries_pass(name):
    res = validate_sql(OK[name])
    assert res.ok, res.errors
    assert "LIMIT" in res.sql.upper()


@pytest.mark.parametrize("name", BAD)
def test_dangerous_or_unknown_sql_rejected(name):
    res = validate_sql(BAD[name])
    assert not res.ok
    assert res.errors


def test_limit_added_when_missing():
    assert validate_sql("SELECT order_id FROM fact_orders", default_limit=50).sql.endswith("LIMIT 50")


def test_oversized_limit_clamped_and_small_limit_kept():
    assert validate_sql("SELECT order_id FROM fact_orders LIMIT 999999", default_limit=100).sql.endswith("LIMIT 100")
    assert validate_sql("SELECT order_id FROM fact_orders LIMIT 5", default_limit=100).sql.endswith("LIMIT 5")


def test_comments_stripped_when_harmless():
    res = validate_sql("SELECT order_id FROM fact_orders -- the ids\n")
    assert res.ok and "--" not in res.sql


def test_cte_name_not_mistaken_for_table_but_unknown_cte_column_fails():
    res = validate_sql("WITH t AS (SELECT order_id FROM fact_orders) SELECT missing FROM t")
    assert not res.ok
