"""Append-only audit log: one JSON line per /analyze request in logs/audit.jsonl."""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from app.config import PROJECT_ROOT

AUDIT_PATH = PROJECT_ROOT / "logs" / "audit.jsonl"
_lock = threading.Lock()


def write_audit(
    *,
    question: str,
    sql: str | None,
    validation: dict | None,
    row_count: int | None,
    latency_ms: float,
    grounded: bool | None,
    needs_clarification: bool = False,
    error: str | None = None,
    path: Path | None = None,
) -> None:
    record = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "sql": sql,
        "validation": validation,
        "row_count": row_count,
        "latency_ms": round(latency_ms),
        "grounded": grounded,
        "needs_clarification": needs_clarification,
        "error": error,
    }
    path = path or AUDIT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
