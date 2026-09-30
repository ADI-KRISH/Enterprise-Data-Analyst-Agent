"""Plan the analysis: steps plus the queries to run (strong LLM).

Non-diagnose intents get exactly one query. Diagnose ("why") questions may get up to three
(headline change, then breakdowns); each goes through the same validator and executor.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.prompting import DATA_NOTICE, data_block
from app import llm

NODE = "planner"
MAX_DIAGNOSE_QUERIES = 3


class PlannedQuery(BaseModel):
    purpose: str = Field(description="What this query answers, one short sentence")
    description: str = Field(description=(
        "Precise spec for the SQL writer: KPI(s) and exact expression source, tables, joins, "
        "filters with exact stored values, date literals, grouping, ordering, limit and the "
        "output column names"))


class Plan(BaseModel):
    steps: list[str] = Field(description="2-5 short analysis steps")
    queries: list[PlannedQuery] = Field(min_length=1, max_length=MAX_DIAGNOSE_QUERIES)


SYSTEM_PROMPT = f"""You are the planner of a read-only analytics agent over a PostgreSQL star \
schema (synthetic Indian retail, INR). Given the parsed question, the resolved time range and \
retrieved KPI definitions, reporting policies and table cards, write a plan.

Rules:
- Use KPI definitions exactly as documented (e.g. revenue counts Completed orders only and \
uses SUM(oi.line_revenue)). Use the resolved dates as explicit DATE literals; never the clock.
- Sales geography (region/city/state of revenue) is via the selling store: fact_orders.store_id \
-> dim_store -> dim_region.
- Customer names are restricted: never plan to output them; aggregate instead.
- Keep queries aggregated (grouped results, not raw rows). Only use tables/columns in the table cards.
- intent != diagnose: exactly ONE query.
- intent == diagnose ("why did X change"): up to {MAX_DIAGNOSE_QUERIES} queries: (1) the headline \
metric for the current vs comparison period with the question's filters; (2)-(3) the same \
metric split by the most relevant drivers (e.g. product category, store, channel), each \
returning one row per driver value. In diagnose queries name the period columns \
<metric>_prev (comparison period) and <metric>_curr (current period), computed with \
SUM(CASE WHEN <date in period> THEN ... ELSE 0 END), and filter the date range to cover \
both periods.
- intent == definition: one query computing the KPI's current value for the resolved period.

{DATA_NOTICE}"""


def understanding(state: dict) -> dict:
    keys = ("intent", "metrics", "entities", "filters", "group_by", "top_n", "time_range")
    return {k: state.get(k) for k in keys}


def run(state: dict, trace: list[dict]) -> dict:
    ctx = state.get("retrieved_context") or {}
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage("\n".join([
            data_block("user_question", state["question"]),
            data_block("context", understanding(state)),
            data_block("retrieved_docs", ctx.get("text", "")),
        ])),
    ]
    plan = llm.invoke_structured(llm.get_llm_for_node(NODE, json_mode=True), messages, Plan,
                                 node=NODE, trace=trace)
    limit = MAX_DIAGNOSE_QUERIES if state.get("intent") == "diagnose" else 1
    queries = [
        {"id": i, "purpose": q.purpose, "description": q.description, "sql": None,
         "validated_sql": None, "status": "pending", "errors": [], "error_code": None, "result": None}
        for i, q in enumerate(plan.queries[:limit], start=1)
    ]
    return {"plan": {"steps": plan.steps, "queries": [q.model_dump() for q in plan.queries[:limit]]},
            "queries": queries, "_detail": {"queries": len(queries)}}
