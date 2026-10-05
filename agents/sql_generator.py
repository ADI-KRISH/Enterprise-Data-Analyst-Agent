"""SQL generator: plan + schema + KPI docs -> one PostgreSQL SELECT. On retry it receives
the previous SQL plus validator / database / verifier feedback."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app import llm
from app.state import AgentState
from retrieval.retriever import schema_summary


class SqlOut(BaseModel):
    sql: str


SYSTEM = """You write ONE read-only PostgreSQL SELECT statement for schema `analytics`.
Return JSON: {{"sql": "<query>"}}.

Hard rules:
- Single statement, SELECT or WITH ... SELECT only. No comments. No semicolon chaining.
- Only tables/columns listed below; prefix tables with `analytics.`.
- Revenue = SUM(fact_order_items.line_revenue) over orders with fact_orders.status = 'Completed' (see KPI docs for other metrics).
- Use the resolved date window from the plan with literal dates: fact_orders.order_date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'. Never CURRENT_DATE / NOW().
- Region of a sale = dim_customer.region_id -> dim_region.region_name (stores: dim_store.region_id).
- Give every output column a clear snake_case alias. For comparisons use columns named prev_<metric>, curr_<metric>, change_<metric>.
- ORDER BY meaningfully (time ascending for trends, metric descending for rankings). Add LIMIT for rankings (default top 5).
- Do not round in SQL unless asked; do not invent columns.

{schema}

Example (revenue by fiscal quarter, last two):
SELECT d.fiscal_year, d.fiscal_quarter, SUM(i.line_revenue) AS revenue
FROM analytics.fact_orders o
JOIN analytics.fact_order_items i ON i.order_id = o.order_id
JOIN analytics.dim_date d ON d.date = o.order_date
WHERE o.status = 'Completed'
GROUP BY d.fiscal_year, d.fiscal_quarter ORDER BY d.fiscal_year, d.fiscal_quarter

Example (change by category between two windows):
SELECT p.category,
  SUM(i.line_revenue) FILTER (WHERE o.order_date BETWEEN '2025-10-01' AND '2025-12-31') AS prev_revenue,
  SUM(i.line_revenue) FILTER (WHERE o.order_date BETWEEN '2026-01-01' AND '2026-03-31') AS curr_revenue
FROM analytics.fact_orders o
JOIN analytics.fact_order_items i ON i.order_id = o.order_id
JOIN analytics.dim_product p ON p.product_id = i.product_id
WHERE o.status = 'Completed' AND o.order_date BETWEEN '2025-10-01' AND '2026-03-31'
GROUP BY p.category

KPI + data documentation:
{context}"""


def feedback_for(state: AgentState) -> str | None:
    parts = []
    if state.get("validation_errors"):
        parts.append("The validator rejected your previous SQL:\n- " + "\n- ".join(state["validation_errors"]))
    if state.get("execution_error"):
        parts.append(f"The database returned an error: {state['execution_error']}")
    issues = (state.get("verification") or {}).get("issues")
    if issues:
        parts.append("The result was not acceptable:\n- " + "\n- ".join(issues))
    return "\n\n".join(parts) or None


def run(state: AgentState, trace: list[dict]) -> dict:
    user = (f"Question: {state['question']}\nANCHOR_DATE: {state['anchor_date']}\n"
            f"Resolved time window: {json.dumps(state.get('time_range'))}\n"
            f"Plan: {json.dumps(state.get('plan'), default=str)}")
    fb = feedback_for(state)
    if fb and state.get("sql"):
        user += f"\n\nPrevious SQL:\n{state['sql']}\n\n{fb}\nFix the problem and return corrected JSON."
    out = llm.invoke_structured(
        llm.get_llm_for_node("sql_generator", json_mode=True),
        [SystemMessage(SYSTEM.format(schema=schema_summary(), context=state.get("retrieved_context", ""))),
         HumanMessage(user)],
        SqlOut, node="sql_generator", trace=trace,
    )
    return {"sql": out.sql.strip(), "sql_attempts": state.get("sql_attempts", 0) + 1,
            "validation_errors": [], "execution_error": None}
