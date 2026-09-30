"""Deterministic checks on executed results before analysis.

An empty or unusable result sends the query back to the SQL generator once with feedback
(filters may use wrong values, e.g. 'South' instead of 'South India'). After that retry
the result is accepted and the issues are reported so the answer can say "no data".
"""

from __future__ import annotations

from typing import Any

MAX_VERIFY_RETRIES = 1


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(result: dict[str, Any], intent: str | None) -> list[str]:
    records, columns = result.get("records", []), result.get("columns", [])
    if not columns:
        return ["the query returned no columns"]
    if not records:
        return ["the query returned no rows: check filter values (use the exact stored values "
                "from the table cards) and that the date range lies inside the data window"]
    numeric = [c for c in columns if any(_is_number(r.get(c)) for r in records)]
    if intent != "definition" and not numeric:
        return ["the result has no numeric column: the question needs an aggregated metric"]
    if all(all(r.get(c) is None for r in records) for c in columns if c not in numeric) and not numeric:
        return ["every value in the result is NULL"]
    if numeric and all(r.get(c) is None for r in records for c in numeric):
        return ["every metric value is NULL: the filters probably match no data"]
    return []


def run(state: dict, trace: list[dict]) -> dict:
    intent = state.get("intent")
    retries = state.get("verify_retries", 0)
    issues: dict[int, list[str]] = {}
    for q in state.get("queries", []):
        if q["status"] == "executed":
            problems = check(q["result"] or {}, intent)
            if problems:
                issues[q["id"]] = problems
    if issues and retries < MAX_VERIFY_RETRIES:
        queries = [
            {**q, "status": "invalid", "errors": issues[q["id"]]} if q["id"] in issues else q
            for q in state["queries"]
        ]
        return {"queries": queries, "verify_retries": retries + 1,
                "verification": {"ok": False, "issues": issues, "retrying": True},
                "_status": "retry"}
    verification = {"ok": not issues, "issues": issues, "retrying": False}
    return {"verification": verification, "_status": "ok" if not issues else "accepted_with_issues"}
