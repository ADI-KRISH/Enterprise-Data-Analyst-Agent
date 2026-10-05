"""Planner: turn the understanding + retrieved definitions into an executable analysis plan."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm import get_llm_for_node, invoke_structured
from app.state import AgentState
from retrieval.retriever import format_context

SYSTEM = """You plan the SQL analysis for a retail analytics question. Use ONLY the schema and KPI definitions given.
Produce a short plan:
- approach: one sentence.
- tables: tables needed. joins: join conditions. 
- metric_definitions: the exact KPI formula/filters to use (e.g. Revenue = SUM(line_revenue) where status='Completed').
- group_by: dimensions to group by. time_filter: the exact date range(s) as ISO dates, using time_range from the \
understanding (it is already resolved against ANCHOR_DATE).
- comparison: for "why/driver/change/vs" questions, plan a comparison of the current period against previous_start..\
previous_end, broken down by the most relevant dimension (region, category, store, channel); otherwise null.
- steps: ordered list of steps."""


class Plan(BaseModel):
    approach: str
    tables: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    metric_definitions: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    time_filter: str | None = None
    comparison: str | None = None
    steps: list[str] = Field(default_factory=list)


def run(state: AgentState) -> dict[str, Any]:
    trace: list[dict] = []
    understood = {k: state.get(k) for k in ("intent", "metrics", "entities", "filters", "time_range")}
    human = (f"ANCHOR_DATE: {state['anchor_date']}\nQuestion: {state['question']}\n"
             f"Understanding: {json.dumps(understood, default=str)}\n\n{format_context(state.get('retrieved_context', {}))}")
    plan = invoke_structured(get_llm_for_node("planner", json_mode=True),
                             [SystemMessage(SYSTEM), HumanMessage(human)], Plan, node="planner", trace=trace)
    return {"plan": plan.model_dump(), "trace": trace}
