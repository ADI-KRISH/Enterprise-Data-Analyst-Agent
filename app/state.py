"""LangGraph state shared by all agent nodes."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class AgentState(TypedDict, total=False):
    question: str
    anchor_date: str  # ISO date; max(order_date). Relative dates resolve against this.
    intent: str
    entities: dict[str, Any]
    metrics: list[str]
    filters: list[str]
    time_range: dict[str, Any]  # {"description", "start", "end"} (ISO dates) or {}
    retrieved_context: dict[str, Any]  # {"schema": str, "chunks": [{source, heading, text, score}]}
    plan: dict[str, Any]
    sql: str
    validation_errors: list[str]
    sql_attempts: int  # generations in the current round (max settings.max_sql_attempts)
    verify_retried: bool
    result_df: dict[str, Any]  # {"records": [...], "columns": [...], "truncated": bool}
    verification: dict[str, Any]
    analysis: dict[str, Any]
    chart_spec: dict[str, Any] | None
    answer: str
    grounded: bool
    grounding_issues: list[str]
    answer_attempts: int
    needs_clarification: bool
    clarification_question: str
    error: str | None  # set when the pipeline cannot continue; returned to the caller
    ambiguities: list[str]  # unclear items found by `understand`, consumed by `clarify`
    trace: Annotated[list[dict[str, Any]], operator.add]  # nodes return only their new events
    timings: Annotated[dict[str, float], _merge]  # node -> milliseconds
