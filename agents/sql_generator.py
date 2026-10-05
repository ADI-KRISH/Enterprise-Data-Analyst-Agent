"""SQL generator: plan + schema + definitions (+ errors from a failed attempt) -> one PostgreSQL SELECT."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm import get_llm_for_node, invoke_structured
from app.state import AgentState
from retrieval.retriever import format_context

SYSTEM = """You write ONE read-only PostgreSQL SELECT query for a retail analytics question.

Hard rules:
- Exactly one statement, SELECT or WITH ... SELECT. No comments, no semicolons, no DDL/DML, no pg_* functions.
- Use only tables and columns in the SCHEMA. Tables live in schema analytics (unqualified names are fine).
- Always use AS for column aliases. Always alias aggregates with a descriptive snake_case name.
- Use the KPI definitions exactly. Revenue/orders/etc. count only fact_orders.status = 'Completed' unless the question \
asks otherwise. Region = customer's region (join dim_customer -> dim_region) unless the question is about stores.
- Use explicit date literals from the plan's time range (e.g. o.order_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-30'). \
Never use CURRENT_DATE or NOW().
- Amounts are raw INR numbers (no scaling). Percentages are percent numbers (12.5 means 12.5%) in columns named *_pct.
- Output shape (an analysis step reads your column names):
  * time series: a time column named month, quarter, year or date (e.g. TO_CHAR(DATE_TRUNC('month', o.order_date), 'YYYY-MM') AS month), ordered ascending.
  * period comparison / "why" / "driver": ONE row per dimension member with columns current_<metric> and previous_<metric> \
(use SUM(...) FILTER (WHERE o.order_date BETWEEN ... ) AS current_revenue, same for previous_revenue), over the union of both periods.
  * ranking: dimension column + metric, ORDER BY metric DESC, with a LIMIT matching the question.
- Select readable names (region_name, category) rather than only ids. Do not SELECT *.
Return JSON: {"sql": "<the query>", "notes": "<one short sentence>"}."""


class SqlOut(BaseModel):
    sql: str
    notes: str = ""


def run(state: AgentState) -> dict[str, Any]:
    trace: list[dict] = []
    parts = [
        f"ANCHOR_DATE: {state['anchor_date']}",
        f"Question: {state['question']}",
        f"Time range: {json.dumps(state.get('time_range') or {})}",
        f"Plan: {json.dumps(state.get('plan') or {})}",
        format_context(state.get("retrieved_context", {})),
    ]
    if state.get("validation_errors"):
        parts.append("YOUR PREVIOUS QUERY FAILED. Fix every problem below and return a corrected query.\n"
                     f"Previous SQL:\n{state.get('sql', '')}\nProblems:\n- " + "\n- ".join(state["validation_errors"]))
    out = invoke_structured(get_llm_for_node("sql_generator", json_mode=True),
                            [SystemMessage(SYSTEM), HumanMessage("\n\n".join(parts))], SqlOut,
                            node="sql_generator", trace=trace)
    sql = out.sql.strip().rstrip(";").strip()
    trace.append({"node": "sql_generator", "event": "sql", "attempt": state.get("sql_attempts", 0) + 1, "sql": sql})
    return {"sql": sql, "sql_attempts": state.get("sql_attempts", 0) + 1, "trace": trace}
