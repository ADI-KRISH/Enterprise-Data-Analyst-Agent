"""Planner: turn the understood question into an explicit analysis plan (tables, joins,
metric expressions, filters, grouping). The SQL generator follows this plan."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import llm
from app.state import AgentState
from retrieval.retriever import schema_summary


class PlanOut(BaseModel):
    steps: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    metric_definitions: dict[str, str] = Field(default_factory=dict)  # metric -> SQL expression
    filters: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: str | None = None
    compare_periods: bool = False
    output_columns: list[str] = Field(default_factory=list)


SYSTEM = """You are the planning step of a text-to-SQL analytics agent (PostgreSQL, schema \
analytics). Produce a concise plan as JSON: steps, tables, joins, metric_definitions \
(metric -> exact SQL expression following the KPI definitions), filters (as SQL predicates), \
group_by, order_by, compare_periods (true if two periods must be shown side by side), \
output_columns (final column names, snake_case).

Rules: revenue-type metrics use fact_orders.status = 'Completed'. Use the resolved date window \
EXACTLY as given (never today's date). Fiscal quarter labels come from dim_date \
(join fact_orders.order_date = dim_date.date). Region of a sale = dim_customer.region_id unless \
the question is about stores. Avoid row fan-out: count orders with COUNT(DISTINCT order_id). \
For 'why did X change' questions, compare the current window to previous_period and break the \
change down by the most relevant dimensions (category, region, store, channel, segment) with \
prev/curr/change columns.

{schema}

KPI + data documentation:
{context}"""


def run(state: AgentState, trace: list[dict]) -> dict:
    understanding = {k: state.get(k) for k in ("intent", "entities", "metrics")}
    understanding["filters"] = {k: v for k, v in (state.get("filters") or {}).items()
                                if not k.startswith("_")}
    understanding["time_window"] = state.get("time_range")
    plan = llm.invoke_structured(
        llm.get_llm_for_node("planner", json_mode=True),
        [SystemMessage(SYSTEM.format(schema=schema_summary(), context=state.get("retrieved_context", ""))),
         HumanMessage(f"Question: {state['question']}\nANCHOR_DATE: {state['anchor_date']}\n"
                      f"Understanding: {json.dumps(understanding, default=str)}")],
        PlanOut, node="planner", trace=trace,
    )
    return {"plan": plan.model_dump()}
