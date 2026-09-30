-- analyst_ro: the ONLY role agent code connects as. SELECT on schema analytics, nothing else.
-- Run as the admin role via database/init_roles.sh, which supplies psql variables:
--   :ro_password  (from ANALYST_RO_PASSWORD)   :dbname  (from POSTGRES_DB)
\set ON_ERROR_STOP on

-- Create or update the role (idempotent).
SELECT format('CREATE ROLE analyst_ro LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_ro') \gexec
ALTER ROLE analyst_ro WITH LOGIN PASSWORD :'ro_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 20;

-- Session defaults: read-only transactions, 15s statement timeout, analytics first.
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
ALTER ROLE analyst_ro SET statement_timeout = '15s';
ALTER ROLE analyst_ro SET search_path = analytics;

-- Database: only explicitly granted roles may connect; no TEMP tables for anyone implicit.
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO analyst_ro;

-- Schema public: no usage / create for PUBLIC (so analyst_ro gets nothing there).
REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- Schema analytics: USAGE + SELECT on tables, nothing else.
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;
GRANT USAGE ON SCHEMA analytics TO analyst_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM PUBLIC;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analyst_ro;
-- Tables the admin creates later in analytics are also readable (and only readable).
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analyst_ro;
