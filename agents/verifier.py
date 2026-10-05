"""Result verification (deterministic): is the result usable for answering the question?"""

from __future__ import annotations

from typing import Any

from analytics.analysis import classify_columns
from agents.executor import payload_to_df
from app.config import get_settings
from app.state import AgentState


def verify_result(payload: dict[str, Any], metrics: list[str] | None = None) -> dict[str, Any]:
    df = payload_to_df(payload)
    issues: list[str] = []
    warnings: list[str] = []

    if df.empty:
        issues.append("The query returned no rows. Check filters (status, date range, entity spelling) and joins.")
    else:
        numeric = classify_columns(df)["numeric"]
        if metrics and not numeric:
            issues.append("The result has no numeric metric column although a metric was requested.")
        elif numeric and df[numeric].isna().all().all():
            issues.append("All metric values are NULL. Check joins and filters.")
        if len(df) >= get_settings().default_row_limit:
            warnings.append(f"Result reached the {get_settings().default_row_limit}-row limit and may be truncated.")
    return {"ok": not issues, "action": "pass" if not issues else "retry", "issues": issues, "warnings": warnings,
            "row_count": int(len(df))}


def run(state: AgentState) -> dict[str, Any]:
    v = verify_result(state["result_df"], state.get("metrics"))
    v["retrying"] = v["action"] == "retry" and not state.get("verify_retried")
    update: dict[str, Any] = {"verification": v,
                              "trace": [{"node": "verify", "event": "verification", "ok": v["ok"],
                                         "issues": v["issues"], "retrying": v["retrying"]}]}
    if v["retrying"]:
        # one more SQL round with a fresh attempt budget, told what was wrong
        update |= {"verify_retried": True, "sql_attempts": 0, "validation_errors": v["issues"]}
    return update
