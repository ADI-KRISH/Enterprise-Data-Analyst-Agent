"""LangGraph workflow (CLAUDE.md "Graph").

input_guard → (not analytics → refuse → END) → understand → retrieve
→ (clarify → output_guard → END) → plan → generate_sql → validate ⟲ (errors → generate_sql,
max MAX_SQL_ATTEMPTS) → execute ⟲ (cost/DB errors → generate_sql) → verify ⟲ (empty or
unusable → generate_sql once) → analyze → visualize → respond → grounding_check ⟲
(ungrounded → respond once) → output_guard → END

Every node is wrapped by `_instrument`: it records a trace event {node, status, latency_ms,
model?} (plus the LLM-call events of that node) and accumulates `timings`. An exception in
a node is written to the audit log and becomes `error`; routing then goes straight to
output_guard, which returns the generic error. Trace events never contain error text.
"""

from __future__ import annotations

import time
import traceback
from functools import lru_cache
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from agents import (
    clarifier, grounding, input_guard, output_guard, planner, responder, sql_generator,
    understand, verifier,
)
from agents.executor import get_executor
from agents.sql_validator import validate
from analytics import analysis
from app.audit import audit, new_request_id
from app.config import get_settings
from app.state import AgentState
from visualization import charts

MAX_RESPONSE_ATTEMPTS = 2  # first answer + one regeneration if ungrounded
RECURSION_LIMIT = 60

NodeFn = Callable[[dict, list[dict]], dict]


def _instrument(name: str, fn: NodeFn) -> Callable[[AgentState], dict]:
    def node(state: AgentState) -> dict:
        started = time.perf_counter()
        llm_events: list[dict] = []
        try:
            update = fn(dict(state), llm_events) or {}
            status = update.pop("_status", "ok")
            detail = update.pop("_detail", None)
        except Exception as exc:  # noqa: BLE001 - full error to the audit log only
            audit("node_error", state.get("request_id", ""), node=name,
                  error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
            update, status, detail = {"error": f"{name}_failed"}, "error", None
        ms = round((time.perf_counter() - started) * 1000, 1)
        event: dict[str, Any] = {"node": name, "event": "node", "status": status, "latency_ms": ms}
        models = [e["model"] for e in llm_events if e.get("model")]
        if models:
            event["model"] = models[-1]
        if detail:
            event["detail"] = detail
        update["trace"] = [*llm_events, event]
        update["timings"] = {name: ms}
        return update

    return node


# ----------------------------------------------------------------------------- nodes

def retrieve_node(state: dict, trace: list[dict]) -> dict:
    from retrieval.retriever import retrieve

    # Enrich the search text with the parsed metrics so KPI chunks rank well.
    query = " ".join([state["question"], *state.get("metrics", [])])
    r = retrieve(query)
    ctx = {
        "kpis": [{"kpi_name": c.kpi_name, "section": c.section, "score": round(c.score, 3), "text": c.text}
                 for c in r.kpis],
        "policies": [{"section": c.section, "text": c.text} for c in r.policies],
        "tables": [{"table": c.table, "text": c.text} for c in r.tables],
        "text": r.as_text(),
    }
    return {"retrieved_context": ctx,
            "_detail": {"kpis": [c.kpi_name for c in r.kpis], "tables": r.table_names}}


def _attempt_budget(state: dict) -> int:
    return get_settings().max_sql_attempts + state.get("verify_retries", 0)


def _settle(state: dict, queries: list[dict]) -> dict:
    """After validate/execute: decide retry vs give up, keeping partial diagnose results."""
    invalid = [q for q in queries if q["status"] == "invalid"]
    errors = [f"query {q['id']}: {e}" for q in invalid for e in q.get("errors", [])]
    update: dict[str, Any] = {"queries": queries, "validation_errors": errors}
    if invalid and state.get("sql_attempts", 0) >= _attempt_budget(state):
        survivors = [q for q in queries if q["status"] in ("valid", "executed")]
        if survivors:  # diagnose: continue with the queries that worked
            update["queries"] = [{**q, "status": "failed"} if q["status"] == "invalid" else q
                                 for q in queries]
            update["_status"] = "partial"
        else:
            update["error"] = "sql_failed"
            update["_status"] = "gave_up"
    elif invalid:
        update["_status"] = "retry"
    return update


def validate_node(state: dict, trace: list[dict]) -> dict:
    queries = []
    for q in state["queries"]:
        if q["status"] == "pending":
            res = validate(q["sql"] or "")
            q = ({**q, "status": "valid", "validated_sql": res.normalized_sql, "errors": []} if res.ok
                 else {**q, "status": "invalid", "errors": res.errors, "error_code": "validation_failed"})
            if not res.ok:
                audit("sql_rejected", state["request_id"], query_id=q["id"], sql=q["sql"], errors=res.errors)
        queries.append(q)
    return _settle(state, queries)


def execute_node(state: dict, trace: list[dict]) -> dict:
    executor = get_executor()
    queries = []
    for q in state["queries"]:
        if q["status"] == "valid":
            res = executor.run(q["validated_sql"], state["request_id"])
            if res.ok:
                q = {**q, "status": "executed", "error_code": None, "validated_sql": res.sql,
                     "result": {"columns": res.columns, "records": res.records,
                                "row_count": res.row_count, "truncated": res.truncated}}
            else:
                errors = res.validation_errors or [
                    f"the query failed when executed ({res.error_code}); simplify it and check "
                    "joins, GROUP BY and types"
                ]
                q = {**q, "status": "invalid", "errors": errors, "error_code": res.error_code}
        queries.append(q)
    update = _settle(state, queries)
    executed = [q for q in update["queries"] if q["status"] == "executed"]
    if executed:
        update["sql"] = ";\n\n".join(q["validated_sql"] for q in executed)
        update["result_df"] = executed[0]["result"]
    return update


def analyze_node(state: dict, trace: list[dict]) -> dict:
    return {"analysis": analysis.analyze(state["queries"])}


def visualize_node(state: dict, trace: list[dict]) -> dict:
    result = state.get("result_df") or {}
    return {"chart_spec": charts.build(result, state.get("intent"))}


def grounding_node(state: dict, trace: list[dict]) -> dict:
    results = [q["result"] for q in state["queries"] if q.get("status") == "executed" and q.get("result")]
    g = grounding.check(state.get("answer") or "", results, state.get("analysis"), state["question"])
    update = {"grounded": g.grounded, "grounding": g.as_dict()}
    if not g.grounded:
        update["_status"] = "ungrounded"
        update["_detail"] = {"unsupported": len(g.unsupported)}
    return update


# ---------------------------------------------------------------------------- routing

def _failed(state: AgentState) -> bool:
    return bool(state.get("error"))


def route_guard(state: AgentState) -> str:
    return "understand" if state.get("guard_category") == "analytics" and not state.get("refused") else "refuse"


def route_after_retrieve(state: AgentState) -> str:
    if _failed(state):
        return "output_guard"
    return "clarify" if state.get("needs_clarification") else "plan"


def route_sql(state: AgentState) -> str:
    """Shared by validate and execute: retry generation, continue, or give up."""
    if _failed(state):
        return "output_guard"
    if any(q["status"] == "invalid" for q in state.get("queries", [])):
        return "generate_sql"
    return "next"


def route_verify(state: AgentState) -> str:
    if _failed(state):
        return "output_guard"
    return "generate_sql" if any(q["status"] == "invalid" for q in state.get("queries", [])) else "analyze"


def route_grounding(state: AgentState) -> str:
    if _failed(state):
        return "output_guard"
    if not state.get("grounded") and state.get("response_attempts", 0) < MAX_RESPONSE_ATTEMPTS:
        return "respond"
    return "output_guard"


def _ok_or_out(next_node: str) -> Callable[[AgentState], str]:
    return lambda state: "output_guard" if _failed(state) else next_node


# ------------------------------------------------------------------------------ graph

def build_graph():
    g = StateGraph(AgentState)
    nodes: dict[str, NodeFn] = {
        "input_guard": input_guard.run,
        "refuse": input_guard.refuse,
        "understand": understand.run,
        "retrieve": retrieve_node,
        "clarify": clarifier.run,
        "plan": planner.run,
        "generate_sql": sql_generator.run,
        "validate": validate_node,
        "execute": execute_node,
        "verify": verifier.run,
        "analyze": analyze_node,
        "visualize": visualize_node,
        "respond": responder.run,
        "grounding_check": grounding_node,
        "output_guard": output_guard.run,
    }
    for name, fn in nodes.items():
        g.add_node(name, _instrument(name, fn))

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", route_guard, ["understand", "refuse"])
    g.add_edge("refuse", END)
    g.add_conditional_edges("understand", _ok_or_out("retrieve"), ["retrieve", "output_guard"])
    g.add_conditional_edges("retrieve", route_after_retrieve, ["clarify", "plan", "output_guard"])
    g.add_edge("clarify", "output_guard")
    g.add_conditional_edges("plan", _ok_or_out("generate_sql"), ["generate_sql", "output_guard"])
    g.add_conditional_edges("generate_sql", _ok_or_out("validate"), ["validate", "output_guard"])
    g.add_conditional_edges("validate", route_sql,
                            {"generate_sql": "generate_sql", "next": "execute", "output_guard": "output_guard"})
    g.add_conditional_edges("execute", route_sql,
                            {"generate_sql": "generate_sql", "next": "verify", "output_guard": "output_guard"})
    g.add_conditional_edges("verify", route_verify, ["generate_sql", "analyze", "output_guard"])
    g.add_conditional_edges("analyze", _ok_or_out("visualize"), ["visualize", "output_guard"])
    g.add_conditional_edges("visualize", _ok_or_out("respond"), ["respond", "output_guard"])
    g.add_conditional_edges("respond", _ok_or_out("grounding_check"), ["grounding_check", "output_guard"])
    g.add_conditional_edges("grounding_check", route_grounding, ["respond", "output_guard"])
    g.add_edge("output_guard", END)
    return g.compile()


@lru_cache
def get_graph():
    return build_graph()


# -------------------------------------------------------------------------------- run

def to_response(state: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    """API contract shape (CLAUDE.md "API contract")."""
    result = state.get("result_df") or {}
    error = bool(state.get("error"))
    return {
        "answer": state.get("answer"),
        "sql": None if error or state.get("output_blocked") else state.get("sql"),
        "queries": [] if error or state.get("output_blocked") else [
            {"purpose": q["purpose"], "sql": q["validated_sql"],
             "row_count": (q.get("result") or {}).get("row_count", 0)}
            for q in state.get("queries", []) if q.get("status") == "executed"
        ],
        "data": result.get("records", []),
        "columns": result.get("columns", []),
        "visualization": state.get("chart_spec"),
        "analysis": state.get("analysis"),
        "grounded": state.get("grounded"),
        "needs_clarification": bool(state.get("needs_clarification")),
        "clarification_question": state.get("clarification_question"),
        "refused": bool(state.get("refused")),
        "refusal_reason": state.get("refusal_reason"),
        "output_blocked": state.get("output_blocked"),
        "error": output_guard.GENERIC_ERROR if error else None,
        "time_range": state.get("time_range"),
        "trace": state.get("trace", []),
        "timings": state.get("timings", {}),
        "latency_ms": latency_ms,
        "request_id": state.get("request_id"),
    }


def run_agent(question: str, request_id: str | None = None) -> dict[str, Any]:
    request_id = request_id or new_request_id()
    started = time.perf_counter()
    initial: AgentState = {"request_id": request_id, "question": question, "trace": [], "timings": {},
                           "sql_attempts": 0, "verify_retries": 0, "response_attempts": 0}
    state = get_graph().invoke(initial, {"recursion_limit": RECURSION_LIMIT})
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    response = to_response(state, latency_ms)
    audit(
        "request", request_id, question=question, guard_category=state.get("guard_category"),
        refusal_reason=state.get("refusal_reason"), sql=state.get("sql"),
        validation_errors=state.get("validation_errors"), sql_attempts=state.get("sql_attempts"),
        row_count=(state.get("result_df") or {}).get("row_count"), latency_ms=latency_ms,
        grounded=state.get("grounded"), grounding=state.get("grounding"),
        output_blocked=state.get("output_blocked"), error=state.get("error"),
        needs_clarification=state.get("needs_clarification"),
    )
    return response
