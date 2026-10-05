-- Read-only agent role. Runs after schema.sql in docker-entrypoint-initdb.d.
-- Password comes from the ANALYST_RO_PASSWORD env var (psql 15+ \getenv).
\getenv ro_password ANALYST_RO_PASSWORD

CREATE ROLE analyst_ro LOGIN PASSWORD :'ro_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

GRANT CONNECT ON DATABASE analytics TO analyst_ro;
GRANT USAGE ON SCHEMA analytics TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analyst_ro;

REVOKE ALL ON SCHEMA public FROM analyst_ro;
ALTER ROLE analyst_ro SET search_path = analytics;
ALTER ROLE analyst_ro SET statement_timeout = '15s';
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
