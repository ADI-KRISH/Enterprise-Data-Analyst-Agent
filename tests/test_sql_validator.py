import pytest

from agents.sql_validator import validate_sql

OK = "SELECT region_name FROM analytics.dim_region"


def test_valid_adds_limit():
    r = validate_sql(OK, default_limit=50)
    assert r.ok and r.limit_added and r.sql.endswith("LIMIT 50")


def test_existing_limit_kept():
    r = validate_sql(OK + " LIMIT 5")
    assert r.ok and not r.limit_added and "LIMIT 5" in r.sql


def test_unqualified_table_and_cte_and_join():
    r = validate_sql(
        "WITH s AS (SELECT o.store_id, SUM(i.line_revenue) AS rev FROM fact_orders o "
        "JOIN fact_order_items i ON i.order_id = o.order_id WHERE o.status='Completed' "
        "GROUP BY o.store_id) SELECT st.city, s.rev FROM s JOIN dim_store st ON st.store_id = s.store_id "
        "ORDER BY s.rev DESC"
    )
    assert r.ok, r.errors


def test_union_ok():
    assert validate_sql("SELECT region_id FROM dim_region UNION SELECT region_id FROM dim_store").ok


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM analytics.dim_region",
        "INSERT INTO analytics.dim_region VALUES (1,'a','b')",
        "UPDATE analytics.dim_region SET zone='x'",
        "DROP TABLE analytics.dim_region",
        "TRUNCATE analytics.dim_region",
        "CREATE TABLE x (a int)",
        "ALTER TABLE analytics.dim_region ADD COLUMN z int",
        "GRANT SELECT ON analytics.dim_region TO public",
        "COPY analytics.dim_region TO '/tmp/x'",
        "CALL foo()",
        "DO $$ BEGIN END $$",
        "SELECT 1; DROP TABLE analytics.dim_region",
        "SELECT 1; SELECT 2",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('a','b',false)",
        "SELECT * INTO t FROM analytics.dim_region",
        "SELECT /* drop table x; */ 1",
        "",
    ],
)
def test_rejected(sql):
    assert not validate_sql(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM public.users",
        "SELECT * FROM secrets",
        "SELECT nope FROM analytics.dim_region",
        "SELECT r.nope FROM analytics.dim_region r",
        "SELECT region_name FROM analytics.dim_region r JOIN analytics.dim_store s ON s.nope = r.region_id",
    ],
)
def test_allowlist_violations(sql):
    assert not validate_sql(sql).ok
