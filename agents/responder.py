"""Responder: business answer grounded only in structured results (rule 1)."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app import llm
from app.state import AgentState

MAX_ROWS_TO_LLM = 50

SYSTEM = """You are a business analyst writing for executives. Answer the question using ONLY \
the structured data provided. Hard rules:
- Every number you write must appear in `rows` or `analysis` exactly or rounded (amounts are INR; \
you may express large amounts as lakh (1,00,000) or crore (1,00,00,000) with the unit, e.g. ₹12.35 lakh). \
Do not compute new figures yourself, do not estimate, do not use outside knowledge.
- If `rows` is empty, say no data matched and suggest what to adjust. Never fabricate.
- State the time window and filters you used (relative dates are anchored to ANCHOR_DATE, not today).
- If `truncated` is true, mention the result may be partial.
- For 'why' questions, explain using the `drivers` / comparison figures provided and say plainly \
that the data shows what changed, not external causes.
- Be concise: a headline sentence, then 2-5 bullets of key figures. Plain markdown. No SQL."""


def build_payload(state: AgentState) -> dict:
    frame = state.get("result_df") or {"columns": [], "records": []}
    return {
        "question": state["question"],
        "anchor_date": state.get("anchor_date"),
        "time_window": state.get("time_range"),
        "filters": {k: v for k, v in (state.get("filters") or {}).items() if not k.startswith("_")},
        "columns": frame["columns"],
        "rows": frame["records"][:MAX_ROWS_TO_LLM],
        "row_count": len(frame["records"]),
        "truncated": bool((state.get("verification") or {}).get("truncated")
                          or len(frame["records"]) > MAX_ROWS_TO_LLM),
        "analysis": state.get("analysis"),
    }


def run(state: AgentState, trace: list[dict]) -> dict:
    payload = build_payload(state)
    user = "Data:\n" + json.dumps(payload, default=str)
    report = state.get("grounding_report") or {}
    if report.get("ungrounded") and state.get("answer"):
        user += (
            f"\n\nYour previous answer contained numbers not found in the data: "
            f"{report['ungrounded']}. Rewrite it using only numbers present in the data."
        )
    answer = llm.invoke_text(
        llm.get_llm_for_node("responder"),
        [SystemMessage(SYSTEM), HumanMessage(user)], node="responder", trace=trace,
    )
    return {"answer": answer, "answer_attempts": state.get("answer_attempts", 0) + 1}
