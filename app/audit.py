"""Append-only audit log (logs/audit.jsonl): full errors and request details live here only.

Users never see what is written here (CLAUDE.md Security model §4). Everything written is
passed through `redact()` so database URLs / passwords and key-like tokens never land in
the log (§5).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

# scheme://user:password@host -> scheme://user:***@host
_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/@\s]+):[^@\s]*@", re.IGNORECASE)
_PASSWORD_KV = re.compile(r"(password\s*[=:]\s*)(\S+)", re.IGNORECASE)
_KEY_LIKE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|sk-or-[A-Za-z0-9_-]{8,})\b")


def new_request_id() -> str:
    return str(uuid.uuid4())


def _secret_values() -> list[str]:
    """Secret strings from settings that must never appear verbatim in the log."""
    try:
        from app.config import get_settings

        s = get_settings()
    except Exception:  # noqa: BLE001 - redaction must never fail because config is absent
        return []
    values = []
    for secret in (s.openrouter_api_key, s.database_url_ro, s.database_url_admin):
        if secret is None:
            continue
        raw = secret.get_secret_value()
        values.append(raw)
        m = re.search(r"://[^:/@]+:([^@]+)@", raw)
        if m:
            values.append(m.group(1))  # the bare DB password
    return [v for v in values if len(v) >= 4]


def redact(text: str) -> str:
    for value in _secret_values():
        text = text.replace(value, "***")
    text = _URL_CREDENTIALS.sub(r"\1:***@", text)
    text = _PASSWORD_KV.sub(r"\1***", text)
    return _KEY_LIKE.sub("***", text)


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact_obj(v) for v in obj]
    return obj


def audit(event: str, request_id: str, path: Path | None = None, **fields: Any) -> None:
    """Append one JSON line: {ts, event, request_id, **fields} (redacted)."""
    if path is None:
        from app.config import get_settings

        path = get_settings().audit_log_path
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "request_id": request_id,
        **fields,
    }
    line = json.dumps(_redact_obj(record), default=str, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
