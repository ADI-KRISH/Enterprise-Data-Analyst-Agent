#!/bin/bash
# Docker init wrapper: passes ANALYST_RO_PASSWORD into roles.sql.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -v ro_pw="${ANALYST_RO_PASSWORD:?set ANALYST_RO_PASSWORD}" \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /opt/roles.sql
