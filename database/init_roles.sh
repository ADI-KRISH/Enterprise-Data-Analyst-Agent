#!/bin/bash
# docker-entrypoint-initdb.d hook: applies database/roles.sql with the read-only password
# taken from the environment (plain .sql init files cannot read env vars).
set -euo pipefail
: "${ANALYST_RO_PASSWORD:?ANALYST_RO_PASSWORD must be set in .env}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v ro_password="$ANALYST_RO_PASSWORD" -v dbname="$POSTGRES_DB" \
     -f /opt/eda/roles.sql
