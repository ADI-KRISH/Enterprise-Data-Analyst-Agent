"""Write the business answer from executed results only (fast LLM).

The model receives the structured results and the deterministic analysis inside DATA
blocks and is told to use no other numbers; agents/grounding.py then verifies every
number. Phase 5 refines wording and formatting.
"""

from __future__ import annotations

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
- You may shorten money to Indian units by moving the decimal point only: 1 lakh (L) = \
1,00,000 and 1 crore (Cr) = 1,00,00,000 (e.g. 9670000.5 -> ₹96.7 L). Round percentages to 1 decimal.
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


def payload(state: dict) -> dict:
    queries = []
    for q in state.get("queries", []):
        if q.get("status") != "executed":
            continue
        res = q.get("result") or {}
        queries.append({
            "purpose": q["purpose"],
            "columns": res.get("columns", []),
            "rows": res.get("records", [])[:MAX_ROWS_IN_PROMPT],
            "row_count": res.get("row_count", 0),
            "rows_shown": min(res.get("row_count", 0), MAX_ROWS_IN_PROMPT),
            "truncated_by_limit": res.get("truncated", False),
        })
    return {
        "time_period": (state.get("time_range") or {}).get("label"),
        "comparison_period": (state.get("time_range") or {}).get("comparison_label"),
        "results": queries,
        "analysis": state.get("analysis"),
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
