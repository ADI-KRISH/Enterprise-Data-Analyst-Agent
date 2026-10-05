"""Append-only JSONL audit log: one line per /analyze request."""

from __future__ import annotations

import datetime as dt
import json
import threading

from app.config import PROJECT_ROOT

AUDIT_PATH = PROJECT_ROOT / "logs" / "audit.jsonl"
_lock = threading.Lock()


def write_audit(entry: dict) -> None:
    record = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **entry}
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
