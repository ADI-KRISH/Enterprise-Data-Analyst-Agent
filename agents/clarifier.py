"""Clarifier: ask instead of guess when a metric, entity or period is not defined by the data / KPI docs."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm import get_llm_for_node, invoke_structured
from app.state import AgentState
from retrieval.retriever import format_context

SYSTEM = """You decide whether a retail analytics question can be answered from the available data and KPI \
definitions, or whether the user must clarify first.

Ask for clarification ONLY when one of these holds:
1. The metric is not defined in the KPI definitions and cannot be derived from the schema (e.g. churn, lifetime value, \
satisfaction, conversion, forecasts) or is vague ("best", "top" with no metric).
2. A named entity (region, category, store, product) does not exist in the data dictionary and cannot be matched.
3. The time period cannot be resolved with the reporting policies, or the data does not cover it.
4. The question is not about this retail data at all.

Do NOT ask when a documented default exists (fiscal quarters, Completed orders only, customer region, anchor-date \
relative periods). Prefer answering. If you ask, write ONE short question that offers concrete options taken from the \
KPI definitions or data dictionary. Do not include numbers other than those the user already used."""


class Decision(BaseModel):
    needs_clarification: bool
    question: str | None = None
    reason: str | None = None


def run(state: AgentState) -> dict[str, Any]:
    trace: list[dict] = []
    ctx = state.get("retrieved_context", {})
    understood = {k: state.get(k) for k in ("intent", "metrics", "entities", "filters", "time_range", "ambiguities")}
    human = (f"Question: {state['question']}\n\nUnderstanding: {json.dumps(understood, default=str)}\n\n"
             f"{format_context(ctx)}")
    d = invoke_structured(get_llm_for_node("clarifier", json_mode=True),
                          [SystemMessage(SYSTEM), HumanMessage(human)], Decision, node="clarifier", trace=trace)
    needs = bool(d.needs_clarification and d.question)
    return {"needs_clarification": needs, "clarification_question": d.question if needs else "", "trace": trace}
