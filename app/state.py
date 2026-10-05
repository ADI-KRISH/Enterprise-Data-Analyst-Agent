"""Agent state shared by every LangGraph node."""

from __future__ import annotations

from typing import Any, TypedDict


class ResultFrame(TypedDict):
    """JSON-friendly DataFrame: column names + row records."""

    columns: list[str]
    records: list[dict[str, Any]]


class AgentState(TypedDict, total=False):
    question: str
    anchor_date: str  # ISO date = max(order_date); resolves relative dates
    intent: str
    entities: list[str]
    metrics: list[str]
    filters: dict[str, Any]
    time_range: dict[str, Any] | None
    retrieved_context: str
    plan: dict[str, Any]
    sql: str
    validation_errors: list[str]
    sql_attempts: int
    verify_retries: int
    result_df: ResultFrame | None
    verification: dict[str, Any]
    analysis: dict[str, Any]
    chart_spec: dict[str, Any] | None
    answer: str
    grounded: bool
    grounding_report: dict[str, Any]
    answer_attempts: int
    needs_clarification: bool
    clarification_question: str | None
    execution_error: str | None
    trace: list[dict[str, Any]]
    timings: dict[str, float]
