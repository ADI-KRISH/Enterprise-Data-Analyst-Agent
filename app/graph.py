"""LangGraph pipeline.

understand -> retrieve -> clarify (-> END) -> plan -> generate_sql -> validate (loop, max N) -> execute
-> verify (loop once) -> analyze -> visualize -> respond -> grounding_check (regenerate once) -> END

Any node exception is caught, stored in state["error"], and routes to END so the API always returns a
structured response.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from agents import clarifier, executor, planner, responder, sql_generator, sql_validator, understand, verifier
from analytics.analysis import analyze
from app.config import get_settings
from app.state import AgentState
from retrieval.retriever import retrieve
from visualization.charts import build_chart

log = logging.getLogger(__name__)
MAX_ANSWER_ATTEMPTS = 2  # initial answer + one regeneration


# ----------------------------------------------------------------------------- simple nodes

def retrieve_node(state: AgentState) -> dict[str, Any]:
    started = time.perf_counter()
    ctx = retrieve(state["question"] + " " + " ".join(state.get("metrics", [])))
    return {"retrieved_context": ctx, "trace": [{
        "node": "retrieve", "event": "retrieval", "backend": ctx["backend"],
        "sources": [f"{c['source']}: {c['heading']}" for c in ctx["chunks"]],
        "latency_ms": round((time.perf_counter() - started) * 1000)}]}


def clarify_exit_node(state: AgentState) -> dict[str, Any]:
    return {"answer": state.get("clarification_question", "")}


def validate_node(state: AgentState) -> dict[str, Any]:
    res = sql_validator.validate_sql(state.get("sql", ""))
    event = {"node": "validate", "event": "validation", **res.as_dict(), "attempt": state.get("sql_attempts", 0)}
    if res.ok:
        return {"sql": res.sql, "validation_errors": [], "trace": [event]}
    return {"validation_errors": res.errors, "trace": [event]}


def execute_node(state: AgentState) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        df = executor.run_query(state["sql"])
    except executor.ExecutionError as exc:
        return {"validation_errors": [f"Database error: {exc}"],
                "trace": [{"node": "execute", "event": "db_error", "error": str(exc)}]}
    limit_hit = len(df) >= get_settings().default_row_limit
    return {"result_df": executor.df_to_payload(df, limit_hit), "validation_errors": [], "trace": [{
        "node": "execute", "event": "executed", "rows": len(df),
        "latency_ms": round((time.perf_counter() - started) * 1000)}]}


def analyze_node(state: AgentState) -> dict[str, Any]:
    df = executor.payload_to_df(state["result_df"])
    return {"analysis": analyze(df, state.get("intent")) if not df.empty else {"row_count": 0}}


def visualize_node(state: AgentState) -> dict[str, Any]:
    df = executor.payload_to_df(state["result_df"])
    try:
        spec = build_chart(df, state.get("intent")) if not df.empty else None
    except Exception as exc:  # a chart failure must never fail the answer
        log.warning("chart failed: %s", exc)
        spec = None
    return {"chart_spec": spec}


# ----------------------------------------------------------------------------- wrapper + routing

def _instrument(name: str, fn: Callable[[AgentState], dict[str, Any]]) -> Callable[[AgentState], dict[str, Any]]:
    def wrapped(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            update = fn(state)
        except Exception as exc:
            log.exception("node %s failed", name)
            update = {"error": f"{name} failed: {type(exc).__name__}: {str(exc)[:300]}",
                      "trace": [{"node": name, "event": "error", "error": str(exc)[:300]}]}
        update = dict(update)
        update["timings"] = {name: round((time.perf_counter() - started) * 1000)}
        return update
    return wrapped


def _guard(route: Callable[[AgentState], str]) -> Callable[[AgentState], str]:
    return lambda s: "fail" if s.get("error") else route(s)


def _after_clarify(s: AgentState) -> str:
    return "clarify_exit" if s.get("needs_clarification") else "plan"


def _after_validate(s: AgentState) -> str:
    if not s.get("validation_errors"):
        return "execute"
    return "generate_sql" if s.get("sql_attempts", 0) < get_settings().max_sql_attempts else "fail_sql"


def _after_execute(s: AgentState) -> str:
    return _after_validate(s) if s.get("validation_errors") else "verify"


def _after_verify(s: AgentState) -> str:
    return "generate_sql" if (s.get("verification") or {}).get("retrying") else "analyze"


def _after_grounding(s: AgentState) -> str:
    return "end" if s.get("grounded") or s.get("answer_attempts", 0) >= MAX_ANSWER_ATTEMPTS else "respond"


def fail_sql_node(state: AgentState) -> dict[str, Any]:
    return {"error": "Could not produce a valid, executable SQL query after "
                     f"{state.get('sql_attempts', 0)} attempts: " + "; ".join(state.get("validation_errors", []))[:500]}


@lru_cache
def build_graph():
    g = StateGraph(AgentState)
    nodes = {
        "understand": understand.run, "retrieve": retrieve_node, "clarify": clarifier.run,
        "clarify_exit": clarify_exit_node, "plan": planner.run, "generate_sql": sql_generator.run,
        "validate": validate_node, "execute": execute_node, "verify": verifier.run,
        "analyze": analyze_node, "visualize": visualize_node, "respond": responder.run,
        "grounding_check": responder.grounding_check, "fail_sql": fail_sql_node,
    }
    for name, fn in nodes.items():
        g.add_node(name, _instrument(name, fn))

    g.add_edge(START, "understand")
    for a, b in [("understand", "retrieve"), ("retrieve", "clarify"), ("plan", "generate_sql"),
                 ("generate_sql", "validate"), ("analyze", "visualize"), ("visualize", "respond"),
                 ("respond", "grounding_check")]:
        g.add_conditional_edges(a, _guard(lambda s, b=b: b), {b: b, "fail": END})
    g.add_conditional_edges("clarify", _guard(_after_clarify), {"clarify_exit": "clarify_exit", "plan": "plan", "fail": END})
    g.add_conditional_edges("validate", _guard(_after_validate),
                            {"execute": "execute", "generate_sql": "generate_sql", "fail_sql": "fail_sql", "fail": END})
    g.add_conditional_edges("execute", _guard(_after_execute),
                            {"verify": "verify", "generate_sql": "generate_sql", "fail_sql": "fail_sql", "fail": END})
    g.add_conditional_edges("verify", _guard(_after_verify), {"generate_sql": "generate_sql", "analyze": "analyze", "fail": END})
    g.add_conditional_edges("grounding_check", _guard(_after_grounding), {"respond": "respond", "end": END, "fail": END})
    g.add_edge("clarify_exit", END)
    g.add_edge("fail_sql", END)
    return g.compile()


def run_agent(question: str) -> AgentState:
    """Run the full pipeline for one question and return the final state."""
    started = time.perf_counter()
    initial: AgentState = {"question": question.strip(), "trace": [], "timings": {}, "sql_attempts": 0,
                           "answer_attempts": 0, "verify_retried": False, "error": None}
    try:
        initial["anchor_date"] = executor.get_anchor_date().isoformat()
    except Exception as exc:
        return {**initial, "error": f"Database unavailable: {str(exc)[:300]}", "grounded": False}
    final = build_graph().invoke(initial, config={"recursion_limit": 60})
    final["timings"] = {**final.get("timings", {}), "total": round((time.perf_counter() - started) * 1000)}
    return final
