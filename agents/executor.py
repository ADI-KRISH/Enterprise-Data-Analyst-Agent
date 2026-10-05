"""Read-only execution against PostgreSQL as `analyst_ro`.

Sessions set statement_timeout=15s and default_transaction_read_only=on. Every query is
dry-run with EXPLAIN (no ANALYZE) before the real execution.
"""

from __future__ import annotations

import datetime as dt
import decimal
import time
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.state import ResultFrame

STATEMENT_TIMEOUT_MS = 15_000


@dataclass
class ExecutionResult:
    ok: bool
    df: pd.DataFrame | None = None
    error: str | None = None
    stage: str | None = None  # "explain" | "execute"
    elapsed_ms: int = 0


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url_ro,
        pool_pre_ping=True,
        connect_args={
            "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
            "-c default_transaction_read_only=on -c search_path=analytics"
        },
    )


def execute_sql(sql: str, engine: Engine | None = None) -> ExecutionResult:
    """EXPLAIN dry-run, then run. `sql` must already have passed validate_sql."""
    engine = engine or get_engine()
    started = time.perf_counter()
    stage = "explain"
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"EXPLAIN {sql}"))
            stage = "execute"
            df = pd.read_sql_query(text(sql), conn)
            conn.rollback()
    except SQLAlchemyError as exc:
        msg = str(getattr(exc, "orig", exc)).strip().splitlines()[0][:400]
        return ExecutionResult(False, error=msg, stage=stage,
                               elapsed_ms=round((time.perf_counter() - started) * 1000))
    return ExecutionResult(True, df=df, elapsed_ms=round((time.perf_counter() - started) * 1000))


def get_anchor_date(engine: Engine | None = None) -> dt.date:
    """ANCHOR_DATE = MAX(order_date): relative dates resolve against it, not the clock."""
    with (engine or get_engine()).connect() as conn:
        value = conn.execute(text("SELECT MAX(order_date) FROM analytics.fact_orders")).scalar()
    if value is None:
        raise RuntimeError("analytics.fact_orders is empty; run `python -m database.seed`")
    return value


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
        return v.isoformat()[:10] if not isinstance(v, dt.datetime) else v.isoformat()
    if pd.isna(v) if not isinstance(v, (list, dict)) else False:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def df_to_frame(df: pd.DataFrame) -> ResultFrame:
    records = [{c: _jsonable(v) for c, v in row.items()} for row in df.to_dict("records")]
    return {"columns": [str(c) for c in df.columns], "records": records}


def frame_to_df(frame: ResultFrame | None) -> pd.DataFrame:
    if not frame:
        return pd.DataFrame()
    return pd.DataFrame.from_records(frame["records"], columns=frame["columns"])
