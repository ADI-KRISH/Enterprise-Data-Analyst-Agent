"""Read-only PostgreSQL execution (role analyst_ro, statement_timeout 15s, read-only transactions)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from functools import lru_cache
from typing import Any

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from app.config import get_settings

STATEMENT_TIMEOUT_MS = 15_000


class ExecutionError(RuntimeError):
    """The database rejected or failed the query (message is safe to show to the SQL generator)."""


@lru_cache
def get_engine() -> Engine:
    # Only the read-only URL is ever used by agent code.
    return create_engine(
        get_settings().database_url_ro,
        pool_pre_ping=True,
        connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS} -c default_transaction_read_only=on"},
    )


def _short(exc: Exception) -> str:
    return str(getattr(exc, "orig", exc)).strip().splitlines()[0][:400]


def explain(sql: str, engine: Engine | None = None) -> None:
    """Dry-run with EXPLAIN (no execution). Raises ExecutionError on planner errors."""
    try:
        with (engine or get_engine()).connect() as conn:
            conn.execute(text("EXPLAIN " + sql))
    except Exception as exc:
        raise ExecutionError(_short(exc)) from exc


def run_query(sql: str, engine: Engine | None = None) -> pd.DataFrame:
    """EXPLAIN first, then execute. Returns a DataFrame; raises ExecutionError."""
    engine = engine or get_engine()
    explain(sql, engine)
    try:
        with engine.connect() as conn:
            return pd.read_sql_query(text(sql), conn)
    except Exception as exc:
        raise ExecutionError(_short(exc)) from exc


def _jsonable(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
        return value.isoformat()[:10] if not isinstance(value, dt.datetime) else value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def df_to_payload(df: pd.DataFrame, limit_hit: bool = False) -> dict[str, Any]:
    records = [{c: _jsonable(v) for c, v in row.items()} for row in df.to_dict("records")]
    return {"records": records, "columns": [str(c) for c in df.columns], "truncated": limit_hit}


def payload_to_df(payload: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(payload.get("records", []), columns=payload.get("columns"))


@lru_cache
def get_anchor_date() -> dt.date:
    """ANCHOR_DATE = max(order_date). All relative dates resolve against it, not the system clock."""
    with get_engine().connect() as conn:
        value = conn.execute(text("SELECT max(order_date) FROM fact_orders")).scalar()
    if value is None:
        raise ExecutionError("fact_orders is empty; run `python -m database.seed`")
    return value
