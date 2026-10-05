"""Responder: grounded business answer from structured results only (no raw freedom to estimate)."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from analytics.formatting import format_analysis, format_cell, is_money_col
from agents.executor import payload_to_df
from app.llm import get_llm_for_node, invoke_text
from app.state import AgentState

MAX_ROWS_FOR_LLM = 40

SYSTEM = """You are a business analyst writing the final answer to a question, using ONLY the RESULT TABLE and \
ANALYSIS below. They come from an executed SQL query and deterministic Pandas calculations.

Strict rules:
- Every number you write must be copied from the RESULT TABLE or ANALYSIS exactly as displayed (same digits, same \
units such as ₹4.52 crore, ₹12.40 lakh, 37.9%). NEVER calculate, estimate, round differently, convert units, or add \
numbers yourself. If a figure you would like is not present, leave it out.
- Do not invent causes. For "why" questions describe where the change happened (which region/category/store moved, \
from/to values, and the share of movement) and say the data shows where, not proof of why. Mention seed-independent \
facts only if they appear in the table.
- State the period you used (use the time range label verbatim) and the basis (e.g. Completed orders only).
- If the table is empty, say no matching data was found and what was searched. If the result is truncated, say so.
- Be concise: a one-sentence headline answer, then at most 5 short bullets. Plain markdown. No tables. No code."""


def build_facts(state: AgentState) -> str:
    payload = state.get("result_df", {})
    df = payload_to_df(payload)
    rows = []
    for rec in df.head(MAX_ROWS_FOR_LLM).to_dict("records"):
        rows.append(" | ".join(f"{c}: {format_cell(c, rec[c])}" for c in df.columns))
    money = any(is_money_col(c) for c in df.columns)
    analysis = format_analysis({k: v for k, v in (state.get("analysis") or {}).items()
                                if k not in ("columns", "intent", "stats")}, money)
    parts = [
        f"QUESTION: {state['question']}",
        f"TIME RANGE: {(state.get('time_range') or {}).get('description') or 'not restricted'}"
        + (f" ({state['time_range']['start']} to {state['time_range']['end']})" if state.get("time_range") else ""),
        f"ROW COUNT: {len(df)}" + (" (truncated at the row limit)" if payload.get("truncated") else ""),
        "RESULT TABLE (one row per line):\n" + ("\n".join(rows) if rows else "(no rows)"),
    ]
    if len(df) > MAX_ROWS_FOR_LLM:
        parts.append(f"(only the first {MAX_ROWS_FOR_LLM} rows are shown)")
    if analysis:
        parts.append("ANALYSIS:\n" + json.dumps(analysis, ensure_ascii=False))
    return "\n\n".join(parts)


def run(state: AgentState) -> dict[str, Any]:
    trace: list[dict] = []
    human = build_facts(state)
    if state.get("grounding_issues"):
        human += ("\n\nYour previous answer contained numbers that are NOT in the data above: "
                  + ", ".join(state["grounding_issues"])
                  + ". Rewrite the answer using only numbers shown above, copied verbatim.")
    answer = invoke_text(get_llm_for_node("responder"), [SystemMessage(SYSTEM), HumanMessage(human)],
                         node="responder", trace=trace)
    return {"answer": answer, "answer_attempts": state.get("answer_attempts", 0) + 1, "trace": trace}


def grounding_check(state: AgentState) -> dict[str, Any]:
    from agents.grounding import allowed_values, check_grounding

    allowed = allowed_values(state["question"], state.get("result_df"), state.get("analysis"),
                             state.get("time_range"), extra_text=[state.get("anchor_date", "")])
    res = check_grounding(state.get("answer", ""), allowed)
    return {
        "grounded": res.grounded, "grounding_issues": res.ungrounded,
        "trace": [{"node": "grounding_check", "event": "grounding", "grounded": res.grounded,
                   "checked": res.checked, "ungrounded": res.ungrounded}],
    }
