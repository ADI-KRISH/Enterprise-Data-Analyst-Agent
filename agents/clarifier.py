"""Clarifier: ask a question instead of guessing when something is undefined (rule 4)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app import llm
from app.state import AgentState


class ClarifyOut(BaseModel):
    needs_clarification: bool
    clarification_question: str | None = None


SYSTEM = """You decide whether an analytics question can be answered from the provided schema \
and KPI documentation without guessing. Ask for clarification when a metric, entity or time \
period is not defined by the data or KPI docs, or is genuinely ambiguous (e.g. "best", \
"performance", an unknown region, "customer lifetime value" which has no data). If the request \
is clearly answerable (defaults are documented: revenue = Completed orders, relative dates use \
ANCHOR_DATE, "top" defaults to 5), set needs_clarification=false.
If clarification is needed, write ONE short, specific question that offers concrete options \
from the data. Return JSON: {"needs_clarification": bool, "clarification_question": str|null}."""


def needs_check(state: AgentState) -> bool:
    f = state.get("filters") or {}
    return bool(f.get("_unknown_terms") or f.get("_out_of_scope"))


def run(state: AgentState, trace: list[dict]) -> dict:
    if not needs_check(state):
        return {"needs_clarification": False, "clarification_question": None}
    f = state.get("filters") or {}
    prompt = (
        f"Question: {state['question']}\nAnchor date: {state.get('anchor_date')}\n"
        f"Intent: {state.get('intent')}  Metrics: {state.get('metrics')}\n"
        f"Terms flagged as unknown: {f.get('_unknown_terms')}  Out of scope: {f.get('_out_of_scope')}\n\n"
        f"Documentation:\n{state.get('retrieved_context', '')}"
    )
    out = llm.invoke_structured(
        llm.get_llm_for_node("clarifier", json_mode=True),
        [SystemMessage(SYSTEM), HumanMessage(prompt)], ClarifyOut, node="clarifier", trace=trace,
    )
    ask = out.needs_clarification and bool(out.clarification_question)
    return {"needs_clarification": ask,
            "clarification_question": out.clarification_question if ask else None}
