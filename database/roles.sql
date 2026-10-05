-- Read-only agent role. Run as the admin user with: psql -v ro_pw=<password> -f roles.sql
-- Idempotent. analyst_ro gets SELECT on schema analytics only.
SELECT format('CREATE ROLE analyst_ro LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'ro_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_ro') \gexec
SELECT format('ALTER ROLE analyst_ro PASSWORD %L', :'ro_pw') \gexec

CREATE SCHEMA IF NOT EXISTS analytics;
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
ALTER ROLE analyst_ro SET statement_timeout = '15s';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA analytics TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analyst_ro;
