"""Verifier: deterministic sanity checks on the executed result (no LLM)."""

from __future__ import annotations

import pandas as pd

from app.config import get_settings
from app.state import AgentState


def verify_frame(frame: dict | None, plan: dict | None = None) -> dict:
    issues: list[str] = []
    if not frame or not frame["records"]:
        issues.append("The query returned no rows; check filters, joins and the date window "
                      "(use the resolved window from ANCHOR_DATE, and the right status filter).")
        return {"ok": False, "row_count": 0, "truncated": False, "issues": issues}
    df = pd.DataFrame.from_records(frame["records"], columns=frame["columns"])
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric:
        issues.append("The result has no numeric column; the question needs a computed metric.")
    elif df[numeric].isna().all().all():
        issues.append("All metric values are NULL; check joins/filters.")
    expected = [c.lower() for c in (plan or {}).get("output_columns", [])]
    missing = [c for c in expected if c not in [x.lower() for x in df.columns]]
    warnings = [f"Planned column(s) missing from result: {', '.join(missing)}"] if missing else []
    truncated = len(df) >= get_settings().default_row_limit
    if truncated:
        warnings.append(f"Result reached the row limit ({len(df)}); it may be truncated.")
    return {"ok": not issues, "row_count": int(len(df)), "truncated": truncated,
            "issues": issues, "warnings": warnings}


def run(state: AgentState, trace: list[dict]) -> dict:
    return {"verification": verify_frame(state.get("result_df"), state.get("plan"))}
