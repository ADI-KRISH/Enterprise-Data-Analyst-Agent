"""Generate PostgreSQL for each pending/failed planned query (strong LLM).

Context is limited to the retrieved schema cards, the retrieved KPI definitions (with their
reference SQL, to be used verbatim), the plan and the resolved time range. On retries the
previous SQL and the validator / executor feedback are included. Output is untrusted: it
always goes through agents/sql_validator.py next.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from typing import Any

from pydantic import BaseModel, model_validator

from agents.prompting import DATA_NOTICE, data_block
from app import llm

NODE = "sql_generator"


class GeneratedQuery(BaseModel):
    id: int
    sql: str


class SQLBatch(BaseModel):
    queries: list[GeneratedQuery]

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_shapes(cls, data: Any) -> Any:
        """Models sometimes return one {id, sql} object or a bare list for a single query."""
        if isinstance(data, list):
            return {"queries": data}
        if isinstance(data, dict) and "queries" not in data and "sql" in data:
            return {"queries": [{"id": data.get("id", 1), "sql": data["sql"]}]}
        return data


SYSTEM_PROMPT = f"""You write PostgreSQL 16 SELECT queries for a read-only analytics agent.

Hard rules (queries breaking them are rejected):
- Exactly one SELECT statement per query (WITH ... SELECT allowed; no recursive CTEs), no \
semicolons, no comments.
- Only the tables and columns in the table cards. Tables unqualified or as analytics.<table>.
- Copy the KPI SQL expressions from the KPI definitions verbatim, including their status filter \
(e.g. revenue = SUM(oi.line_revenue) with o.status = 'Completed').
- Dates: explicit literals from the resolved time range, e.g. o.order_date BETWEEN DATE \
'2026-07-01' AND DATE '2026-09-30'. Never now(), current_date or other clock functions.
- Allowed functions only: count, sum, avg, min, max, stddev, percentile_cont, date_trunc, \
extract, date_part, to_char, make_date, round, abs, ceil, floor, greatest, least, nullif, \
lower, upper, trim, length, concat, substring, CASE, COALESCE, CAST, and window functions \
(row_number, rank, dense_rank, lag, lead, ntile, first_value, last_value, aggregates OVER).
- dim_customer.name must never be an output column (it may be used in WHERE/JOIN/GROUP BY \
or inside COUNT). No SELECT * over dim_customer.
- Give every computed column a clear snake_case alias. Round money and percentages to 2 \
decimals with ROUND(x::numeric, 2) where needed. Aggregate; do not return raw order rows.
- Add ORDER BY for rankings and time series; add LIMIT for top-N.

Return JSON {{"queries": [{{"id": <query id>, "sql": "<sql>"}}]}} with one entry per requested query id.

{DATA_NOTICE}"""


def _context(state: dict) -> str:
    ctx = state.get("retrieved_context") or {}
    kpis = "\n\n".join(k["text"] for k in ctx.get("kpis", []))
    cards = "\n\n".join(t["text"] for t in ctx.get("tables", []))
    return data_block("retrieved_docs", f"# KPI definitions\n\n{kpis}\n\n# Table cards\n\n{cards}")


def pending(state: dict) -> list[dict]:
    return [q for q in state.get("queries", []) if q["status"] in ("pending", "invalid")]


def run(state: dict, trace: list[dict]) -> dict:
    todo = pending(state)
    tr = state.get("time_range") or {}
    requests = []
    for q in todo:
        item = {"id": q["id"], "purpose": q["purpose"], "spec": q["description"]}
        if q.get("sql") and q.get("errors"):
            item |= {"previous_sql": q["sql"], "problems_to_fix": q["errors"]}
        requests.append(item)
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage("\n".join([
            _context(state),
            data_block("user_question", state["question"]),
            data_block("plan", {
                "steps": (state.get("plan") or {}).get("steps", []),
                "time_range": tr,
                "queries_to_write": requests,
            }),
        ])),
    ]
    batch = llm.invoke_structured(llm.get_llm_for_node(NODE, json_mode=True), messages, SQLBatch,
                                  node=NODE, trace=trace)
    by_id = {g.id: g.sql for g in batch.queries}
    if len(todo) == 1 and len(batch.queries) == 1:
        by_id = {todo[0]["id"]: batch.queries[0].sql}  # tolerate a wrong id on a single query
    queries = []
    for q in state["queries"]:
        if q in todo:
            sql = by_id.get(q["id"])
            q = {**q, "sql": sql, "validated_sql": None, "result": None, "error_code": None,
                 "status": "pending" if sql else "invalid",
                 "errors": [] if sql else ["no SQL was returned for this query id"]}
        queries.append(q)
    attempts = state.get("sql_attempts", 0) + 1
    return {"queries": queries, "sql_attempts": attempts,
            "_detail": {"attempt": attempts, "generated": [q["id"] for q in todo]}}
