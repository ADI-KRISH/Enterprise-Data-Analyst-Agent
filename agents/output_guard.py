"""Last graph node: deterministic scan of everything user-facing (CLAUDE.md Guardrails §3).

Scans the answer, clarification question, analysis and returned data/columns (not the
`sql` field, which is returned by design) for:
- restricted values (e.g. customer names), loaded once by trusted code, kept in memory only;
- raw SQL / driver errors and stack traces;
- internals: catalog names, schema-qualified names, connection strings, key-like tokens.
On a hit the response is replaced by the generic error plus request_id; the unredacted text
goes only to the audit log.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from app.audit import audit

GENERIC_ERROR = "Could not answer this question."

SQL_ERROR_PATTERNS = re.compile(
    r"\bERROR:|\bSQLSTATE\b|syntax error at or near|permission denied|\bpsycopg\b|Traceback \(most recent call last\)"
    r"|\bsqlalchemy\.exc\b|\bUndefinedColumn\b|\bUndefinedTable\b",
    re.IGNORECASE,
)
INTERNAL_PATTERNS = re.compile(
    r"\bpg_catalog\b|\binformation_schema\b|\bpg_[a-z_]+\s*\(|\banalytics\.[a-z_]+"
    r"|\b(postgres(ql)?|mysql|sqlite)(\+\w+)?://|\bsk-(or-)?[A-Za-z0-9_-]{12,}"
    r"|\b(api[_-]?key|secret|password)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class _RestrictedValues:
    """Compiled matcher over restricted column values, loaded once (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pattern: re.Pattern[str] | None = None
        self._loaded = False

    def load(self, values: list[str] | None = None) -> None:
        if values is None:
            from agents.executor import get_executor

            values = get_executor().restricted_values()
        names = sorted({v.strip() for v in values if v and len(v.strip()) >= 3}, key=len, reverse=True)
        self._pattern = (re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.IGNORECASE)
                         if names else None)
        self._loaded = True

    def search(self, text: str) -> bool:
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self.load()
        return bool(self._pattern and self._pattern.search(text))


RESTRICTED = _RestrictedValues()


def _texts(state: dict[str, Any]) -> list[tuple[str, str]]:
    """(field, text) pairs that reach the user, excluding the sql field."""
    out = [(f, state.get(f) or "") for f in ("answer", "clarification_question")]
    if state.get("analysis") is not None:
        out.append(("analysis", json.dumps(state["analysis"], default=str, ensure_ascii=False)))
    result = state.get("result_df") or {}
    out.append(("columns", " | ".join(map(str, result.get("columns", [])))))
    out.append(("data", json.dumps(result.get("records", []), default=str, ensure_ascii=False)))
    return [(f, t) for f, t in out if t]


def scan(state: dict[str, Any]) -> str | None:
    """Reason code of the first hit, or None if clean."""
    texts = _texts(state)
    for _field, text in texts:
        if SQL_ERROR_PATTERNS.search(text):
            return "sql_error_leak"
        if INTERNAL_PATTERNS.search(text):
            return "internals_leak"
    try:
        for _field, text in texts:
            if RESTRICTED.search(text):
                return "restricted_value"
    except Exception:  # noqa: BLE001 - cannot check restricted values: fail closed
        return "guard_unavailable"
    return None


def run(state: dict, trace: list[dict]) -> dict:
    if state.get("error"):
        return {"answer": GENERIC_ERROR, "result_df": None, "analysis": None, "chart_spec": None,
                "_status": "error_response"}
    reason = scan(state)
    if reason is None:
        return {"output_blocked": None}
    audit("output_blocked", state.get("request_id", ""), reason=reason,
          answer=state.get("answer"), clarification_question=state.get("clarification_question"),
          columns=(state.get("result_df") or {}).get("columns"),
          data=(state.get("result_df") or {}).get("records"))
    return {
        "output_blocked": reason, "answer": GENERIC_ERROR, "clarification_question": None,
        "result_df": None, "analysis": None, "chart_spec": None, "_status": "blocked",
    }
