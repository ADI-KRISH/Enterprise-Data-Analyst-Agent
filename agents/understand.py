"""Understand: question -> intent, metrics, entities, filters, resolved time range, ambiguities."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.dates import previous_period, resolve_relative_period
from app.llm import get_llm_for_node, invoke_structured
from app.state import AgentState

SYSTEM = """You analyse business questions for a retail analytics agent over a synthetic Indian retail database \
(orders, order items, products, customers, stores, regions; amounts in INR; data from 2024-10-01 to the anchor date).

Today's reference date is ANCHOR_DATE = {anchor} (the latest order date). Never use the system clock. Rules:
- "quarter"/"year" are Indian fiscal (Apr-Mar; Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar) unless the user says calendar.
- "last quarter/month/year" = most recent complete period as of ANCHOR_DATE; "this ..." = period containing it, to date.
- Revenue means Completed orders only. Region means the customer's region.

Extract:
- intent: lookup (single value), ranking (top/bottom N), trend (over time), comparison (A vs B or period vs period), \
breakdown (split by a dimension), diagnostic (why did X change), other.
- metrics: business metrics asked for (e.g. "revenue", "gross margin", "average order value").
- entities: dimension values mentioned, e.g. {{"region": "South India", "category": "Electronics"}}.
- filters: other conditions as short phrases.
- time_phrase: the user's period wording verbatim ("last quarter"), or null. time_start/time_end: ISO dates for that \
period resolved against ANCHOR_DATE, or null if none/unclear.
- ambiguities: things you cannot determine from the question (undefined metric such as churn, unknown entity, \
"best" without a metric, a period that cannot be resolved). Empty list if a reasonable default from the rules above exists."""


class Understanding(BaseModel):
    intent: Literal["lookup", "ranking", "trend", "comparison", "breakdown", "diagnostic", "other"] = "other"
    metrics: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    filters: list[str] = Field(default_factory=list)
    time_phrase: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    ambiguities: list[str] = Field(default_factory=list)


def _iso(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def build_time_range(u: Understanding, question: str, anchor: dt.date) -> dict[str, Any]:
    """Deterministic resolution wins over the model's dates whenever the phrase is recognised."""
    resolved = resolve_relative_period(u.time_phrase or "", anchor) or resolve_relative_period(question, anchor)
    if resolved:
        start, end, label = resolved
    else:
        start, end, label = _iso(u.time_start), _iso(u.time_end), u.time_phrase
        if not (start and end) or start > end:
            return {}
    prev_start, prev_end = previous_period(start, end)
    return {
        "description": label, "start": start.isoformat(), "end": end.isoformat(),
        "previous_start": prev_start.isoformat(), "previous_end": prev_end.isoformat(),
    }


def run(state: AgentState) -> dict[str, Any]:
    trace: list[dict] = []
    anchor = dt.date.fromisoformat(state["anchor_date"])
    llm = get_llm_for_node("understand", json_mode=True)
    u = invoke_structured(
        llm,
        [SystemMessage(SYSTEM.format(anchor=anchor.isoformat())), HumanMessage(state["question"])],
        Understanding, node="understand", trace=trace,
    )
    return {
        "intent": u.intent, "metrics": u.metrics, "entities": u.entities, "filters": u.filters,
        "time_range": build_time_range(u, state["question"], anchor), "ambiguities": u.ambiguities, "trace": trace,
    }
