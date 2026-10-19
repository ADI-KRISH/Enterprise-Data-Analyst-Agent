"""Write the business answer from executed results only (fast LLM).

The model receives the structured results and the deterministic analysis inside DATA
blocks and is told to use no other numbers; agents/grounding.py then verifies every
number. Phase 5 refines wording and formatting.
"""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from agents.prompting import DATA_NOTICE, data_block
from app import llm

NODE = "responder"
MAX_ROWS_IN_PROMPT = 40

SYSTEM_PROMPT = f"""You are a business analyst writing the final answer for a read-only \
sales analytics assistant (synthetic Indian retail data, amounts in INR).

Rules:
- Use ONLY numbers that appear in the <data> block (query results and computed analysis). \
Never estimate, extrapolate or do your own arithmetic; if a figure you want is not there, \
describe it qualitatively instead.
- Money values come with a ready-made `<name>_display` string (e.g. "₹96.7 L"): copy those \
strings exactly instead of converting units yourself. Round percentages to 1 decimal.
- State the time period (use the provided label) and the basis, e.g. "completed orders, net \
of discount", when revenue is involved.
- If the results are empty or flagged with issues, say that no matching data was found; do \
not invent values.
- For "why" questions, explain the change using the breakdown results: name the biggest \
contributors and their change.
- Be concise: 2-5 sentences or a few bullets. No SQL, no table or column names, no headings.
- For definition questions, explain the KPI using the retrieved definition in plain words, \
then give its value from the data.

{DATA_NOTICE}"""


_MONEY_KEYS = {"prev", "curr", "change", "total_prev", "total_curr", "total_change"}
_MONEY_COLUMN = re.compile(r"revenue|sales|cost|margin_amount|profit|aov|amount|value|price", re.I)


def format_inr(x: float) -> str:
    """Deterministic Indian-unit display (done in code so the LLM never converts units)."""
    sign, a = ("-" if x < 0 else ""), abs(x)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.1f} L"
    return f"{sign}₹{a:,.0f}"


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def add_money_display(obj):
    """Add `<key>_display` next to rupee amounts in the analysis dicts."""
    if isinstance(obj, dict):
        out = {k: add_money_display(v) for k, v in obj.items()}
        for k, v in obj.items():
            if k in _MONEY_KEYS and _is_num(v):
                out[f"{k}_display"] = format_inr(v)
        return out
    if isinstance(obj, list):
        return [add_money_display(v) for v in obj]
    return obj


def _row_display(row: dict) -> dict:
    return {**row, **{f"{k}_display": format_inr(v) for k, v in row.items()
                      if _is_num(v) and _MONEY_COLUMN.search(k)}}


def payload(state: dict) -> dict:
    queries = []
    for q in state.get("queries", []):
        if q.get("status") != "executed":
            continue
        res = q.get("result") or {}
        queries.append({
            "purpose": q["purpose"],
            "columns": res.get("columns", []),
            "rows": [_row_display(r) for r in res.get("records", [])[:MAX_ROWS_IN_PROMPT]],
            "row_count": res.get("row_count", 0),
            "rows_shown": min(res.get("row_count", 0), MAX_ROWS_IN_PROMPT),
            "truncated_by_limit": res.get("truncated", False),
        })
    return {
        "time_period": (state.get("time_range") or {}).get("label"),
        "comparison_period": (state.get("time_range") or {}).get("comparison_label"),
        "results": queries,
        "analysis": add_money_display(state.get("analysis")),
        "verification_issues": (state.get("verification") or {}).get("issues") or None,
    }


def run(state: dict, trace: list[dict]) -> dict:
    parts = [data_block("user_question", state["question"]), data_block("data", payload(state))]
    if state.get("intent") == "definition":
        kpis = "\n\n".join(k["text"] for k in (state.get("retrieved_context") or {}).get("kpis", [])[:1])
        parts.append(data_block("retrieved_docs", kpis))
    feedback = (state.get("grounding") or {}).get("unsupported")
    if feedback:
        parts.append(
            "Your previous answer contained numbers that are not in the data: "
            + ", ".join(feedback)
            + ". Rewrite the answer using only numbers from the <data> block."
        )
    messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage("\n".join(parts))]
    answer = llm.invoke_text(llm.get_llm_for_node(NODE), messages, node=NODE, trace=trace)
    return {"answer": answer, "response_attempts": state.get("response_attempts", 0) + 1}
