"""Execute validated SQL as analyst_ro (CLAUDE.md rule 2, Security model §1, §2, §4).

Flow for `QueryExecutor.run(sql)`:
1. `validate(sql)` again (defense in depth; the caller may have validated already);
2. one read-only transaction with `statement_timeout` and `search_path` set locally;
3. `EXPLAIN (FORMAT JSON)` and reject if the plan's total cost exceeds MAX_QUERY_COST;
4. execute the validated SQL and fetch at most MAX_RESULT_ROWS rows;
5. return columns + JSON-safe records.

Failures return a generic message and a request id; the full error goes only to the
audit log (logs/audit.jsonl).
"""

from __future__ import annotations

import datetime as dt
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Any

import psycopg
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

from agents.sql_validator import validate
from app.audit import audit, new_request_id

READ_ONLY_ROLE = "analyst_ro"
GENERIC_ERROR = "Could not answer this question."


class UnsafeConnectionError(RuntimeError):
    """Raised when the executor is pointed at anything other than the read-only role."""


@dataclass
class ExecutionResult:
    ok: bool
    request_id: str
    columns: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False  # hit a row limit we imposed: more rows may exist
    elapsed_ms: float = 0.0
    cost: float | None = None
    sql: str | None = None  # the SQL that was (or would have been) executed
    error: str | None = None  # generic, user-safe
    error_code: str | None = None  # validation_failed | cost_limit | timeout | db_error
    # Internal only: fed back to the SQL generator, never returned to users.
    validation_errors: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("validation_errors")
        return d


# ------------------------------------------------------------------ JSON-safe values

def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() and value.as_tuple().exponent >= 0 \
            else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None  # binary values are never returned
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    return str(value)


def _unique_columns(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        if name in seen:
            seen[name] += 1
            candidate = f"{name}_{seen[name]}"
            while candidate in seen:
                seen[name] += 1
                candidate = f"{name}_{seen[name]}"
            name = candidate
        seen.setdefault(name, 0)
        out.append(name)
    return out


# ----------------------------------------------------------------------- executor

class QueryExecutor:
    def __init__(
        self,
        url: SecretStr | None = None,
        *,
        max_cost: float | None = None,
        max_rows: int | None = None,
        default_limit: int | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        from app.config import get_settings

        s = get_settings()
        url = url or s.database_url_ro
        parsed = make_url(url.get_secret_value())
        if parsed.username != READ_ONLY_ROLE:
            # Never echo the URL: it contains a password.
            raise UnsafeConnectionError(f"executor must connect as {READ_ONLY_ROLE}")
        self.max_cost = max_cost if max_cost is not None else s.max_query_cost
        self.max_rows = max_rows if max_rows is not None else s.max_result_rows
        self.default_limit = min(default_limit or s.default_row_limit, self.max_rows)
        self.timeout_ms = timeout_ms if timeout_ms is not None else s.statement_timeout_ms
        self._engine: Engine = create_engine(
            parsed,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            # Session defaults set at connect time, independent of the role's defaults.
            connect_args={
                "options": (
                    f"-c default_transaction_read_only=on -c statement_timeout={self.timeout_ms}"
                    " -c search_path=analytics"
                ),
                "connect_timeout": 5,
                "application_name": "eda-agent",
            },
        )

    def dispose(self) -> None:
        self._engine.dispose()

    def session_settings(self) -> dict[str, str]:
        """Effective settings inside an executor transaction (used by tests)."""
        with self._engine.connect() as conn:
            self._begin(conn)
            row = conn.exec_driver_sql(
                "SELECT current_user, current_setting('search_path'),"
                " current_setting('transaction_read_only'), current_setting('statement_timeout')"
            ).one()
            conn.rollback()
        return dict(zip(
            ("current_user", "search_path", "transaction_read_only", "statement_timeout"), row
        ))

    def _begin(self, conn) -> None:
        # First statements of the transaction: enforce read-only + timeout locally too.
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
        conn.exec_driver_sql("SET LOCAL search_path = analytics")

    def run(self, sql: str, request_id: str | None = None) -> ExecutionResult:
        request_id = request_id or new_request_id()
        t0 = time.perf_counter()

        def elapsed() -> float:
            return round((time.perf_counter() - t0) * 1000, 2)

        check = validate(sql, default_limit=self.default_limit, max_rows=self.max_rows)
        if not check.ok:
            audit("sql_rejected", request_id, sql=sql, errors=check.errors)
            return ExecutionResult(
                ok=False, request_id=request_id, error=GENERIC_ERROR,
                error_code="validation_failed", validation_errors=check.errors,
                elapsed_ms=elapsed(),
            )
        final_sql = check.normalized_sql
        cost: float | None = None
        try:
            with self._engine.connect() as conn:
                self._begin(conn)
                cur = conn.connection.cursor()
                # Raw DBAPI cursor with no parameters: no placeholder parsing, so '%'
                # and ':' inside literals reach Postgres untouched.
                cur.execute(f"EXPLAIN (FORMAT JSON) {final_sql}")
                plan = cur.fetchone()[0]
                cost = float(plan[0]["Plan"]["Total Cost"])
                if cost > self.max_cost:
                    conn.rollback()
                    audit("sql_cost_rejected", request_id, sql=final_sql, cost=cost,
                          max_cost=self.max_cost)
                    return ExecutionResult(
                        ok=False, request_id=request_id, sql=final_sql, cost=cost,
                        error=GENERIC_ERROR, error_code="cost_limit",
                        validation_errors=[
                            f"query too expensive (estimated cost {cost:.0f} > {self.max_cost:.0f}); "
                            "aggregate more, add filters, or avoid cross joins"
                        ],
                        elapsed_ms=elapsed(),
                    )
                cur.execute(final_sql)
                columns = _unique_columns([d[0] for d in cur.description or []])
                rows = cur.fetchmany(self.max_rows)
                cur.close()
                conn.rollback()
        except (psycopg.Error, DBAPIError) as e:
            orig = e.orig if isinstance(e, DBAPIError) and e.orig is not None else e
            code = "timeout" if getattr(orig, "sqlstate", None) == "57014" else "db_error"  # query_canceled
            audit("sql_error", request_id, sql=final_sql, error_code=code,
                  error=f"{type(orig).__name__}: {orig}", cost=cost)
            return ExecutionResult(
                ok=False, request_id=request_id, sql=final_sql, cost=cost,
                error=GENERIC_ERROR, error_code=code, elapsed_ms=elapsed(),
            )
        except Exception as e:  # noqa: BLE001 - anything else is logged, never shown
            audit("sql_error", request_id, sql=final_sql, error_code="db_error",
                  error=f"{type(e).__name__}: {e}", cost=cost)
            return ExecutionResult(
                ok=False, request_id=request_id, sql=final_sql, cost=cost,
                error=GENERIC_ERROR, error_code="db_error", elapsed_ms=elapsed(),
            )

        truncated = check.limit_imposed and len(rows) >= (check.limit or 0)
        records = [{c: to_json_safe(v) for c, v in zip(columns, row)} for row in rows]
        res = ExecutionResult(
            ok=True, request_id=request_id, columns=columns, records=records,
            row_count=len(records), truncated=truncated, elapsed_ms=elapsed(),
            cost=cost, sql=final_sql,
        )
        audit("sql_executed", request_id, sql=final_sql, cost=cost, row_count=res.row_count,
              truncated=truncated, elapsed_ms=res.elapsed_ms)
        return res


@lru_cache
def get_executor() -> QueryExecutor:
    return QueryExecutor()


def execute(sql: str, request_id: str | None = None) -> ExecutionResult:
    return get_executor().run(sql, request_id)
