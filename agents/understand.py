"""Understand: classify intent and extract entities / metrics / filters / time range."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.time_ranges import TimeRange, resolve_time_range
from app import llm
from app.state import AgentState
from retrieval.build_index import DOCS_DIR
from retrieval.retriever import schema_summary

Intent = Literal["lookup", "ranking", "trend", "comparison", "breakdown", "diagnostic", "other"]


class UnderstandOut(BaseModel):
    intent: Intent = "other"
    entities: list[str] = Field(default_factory=list)  # regions, categories, stores, segments...
    metrics: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)  # e.g. {"region": "South India"}
    time_range: TimeRange = Field(default_factory=TimeRange)
    unknown_terms: list[str] = Field(default_factory=list)
    out_of_scope: bool = False


def kpi_names() -> list[str]:
    text = (DOCS_DIR / "kpi_definitions.md").read_text()
    defined = text.split("## Not defined")[0]
    return re.findall(r"(?m)^## (.+)$", defined)


SYSTEM = """You analyse business questions for an analytics agent over a synthetic Indian retail \
data warehouse (INR). ANCHOR_DATE (latest data date, used for all relative dates) is {anchor}.

{schema}

Defined KPIs: {kpis}.
Not available in the data (set out_of_scope=true and list in unknown_terms): customer lifetime \
value, churn, retention, forecasts, budgets/targets, inventory, marketing spend, footfall, \
conversion rate, GST/tax, profit after overheads, anything outside these tables.

Return JSON with:
- intent: lookup (single figure) | ranking (top/bottom) | trend (over time) | comparison (A vs B, or period vs period) | breakdown (by dimension) | diagnostic (why/what drove a change) | other
- entities: named dimension values mentioned (e.g. "South India", "Electronics")
- metrics: metric names from the defined KPIs (revenue, orders, aov, gross_margin_pct, return_rate...). If no metric is stated for a sales-style question, default to "revenue" and do NOT flag it unknown.
- filters: dimension -> value, e.g. {{"region": "South India", "category": "Electronics"}}
- time_range: {{"kind": one of unspecified|all_time|this_month|last_month|this_quarter|last_quarter|this_fy|last_fy|ytd|last_n_days|last_n_months|fiscal_quarter|fiscal_year|calendar_year|month|custom,
   "n","fiscal_year" (year the Indian FY ends: FY2025-26 = 2026),"quarter" (fiscal 1-4, Q1=Apr-Jun),"year","month","start","end" (ISO),
   "compare_to": none|previous_period|same_period_last_year}}. Use compare_to=previous_period for "why did X drop/grow", "vs last quarter", "growth". "latest quarter" = this_quarter. "last quarter" = last_quarter.
- unknown_terms: metrics / entities / periods in the question that are NOT defined by the data or KPIs, or are genuinely ambiguous (e.g. "best", "performance" with no metric, unknown region/product)
- out_of_scope: true if the question cannot be answered from the tables above."""


def run(state: AgentState, trace: list[dict]) -> dict:
    anchor = state["anchor_date"]
    out = llm.invoke_structured(
        llm.get_llm_for_node("understand", json_mode=True),
        [SystemMessage(SYSTEM.format(anchor=anchor, schema=schema_summary(),
                                     kpis=", ".join(kpi_names()))),
         HumanMessage(state["question"])],
        UnderstandOut, node="understand", trace=trace,
    )
    resolved = resolve_time_range(out.time_range, dt.date.fromisoformat(anchor))
    return {
        "intent": out.intent,
        "entities": out.entities,
        "metrics": out.metrics or ["revenue"],
        "filters": {**out.filters, "_unknown_terms": out.unknown_terms,
                    "_out_of_scope": out.out_of_scope},
        "time_range": resolved,
    }
