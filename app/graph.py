"""LangGraph pipeline.

understand -> retrieve -> clarify? (END) -> plan -> generate_sql -> validate (loop, max 3)
-> execute (loop) -> verify (retry once) -> analyze -> visualize -> respond
-> grounding_check (regenerate once) -> END
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Callable

from langgraph.graph import END, START, StateGraph

from agents import (clarifier, planner, responder, sql_generator, understand, verifier)
from agents.executor import df_to_frame, execute_sql, get_anchor_date
from agents.grounding import check_grounding
from agents.sql_validator import validate_sql
from analytics.analysis import analyze
from app.config import get_settings
from app.state import AgentState
from retrieval.retriever import format_context, retrieve
from visualization.charts import build_chart

NodeFn = Callable[[AgentState, list], dict]


def _node(name: str, fn: NodeFn):
    """Wrap a node: shared trace list, per-node timing, one trace event."""

    def wrapped(state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        started = time.perf_counter()
        updates = fn(state, trace)
        ms = round((time.perf_counter() - started) * 1000)
        event = {"node": name, "event": "node", "latency_ms": ms, **updates.pop("_trace", {})}
        timings = dict(state.get("timings", {}))
        timings[name] = timings.get(name, 0) + ms
        return {**updates, "trace": [*trace, event], "timings": timings}

    wrapped.__name__ = name
    return wrapped


# ----------------------------------------------------------------------------- nodes

def n_understand(state, trace):
    updates = {}
    if not state.get("anchor_date"):
        updates["anchor_date"] = get_anchor_date().isoformat()
        state = {**state, **updates}
    out = understand.run(state, trace)
    return {**updates, **out, "_trace": {"intent": out["intent"], "time_window": out["time_range"]}}


def n_retrieve(state, trace):
    chunks = retrieve(f"{state['question']} {' '.join(state.get('metrics') or [])}", k=6)
    return {"retrieved_context": format_context(chunks),
            "_trace": {"sources": [f"{c['source']}#{c['section']}" for c in chunks]}}


def n_clarify(state, trace):
    out = clarifier.run(state, trace)
    return {**out, "_trace": {"needs_clarification": out["needs_clarification"]}}


def n_plan(state, trace):
    return planner.run(state, trace)


def n_generate_sql(state, trace):
    out = sql_generator.run(state, trace)
    return {**out, "_trace": {"attempt": out["sql_attempts"]}}


def n_validate(state, trace):
    res = validate_sql(state["sql"])
    updates = {"validation_errors": res.errors, "_trace": res.as_dict()}
    if res.ok:
        updates["sql"] = res.sql
    return updates


def n_execute(state, trace):
    res = execute_sql(state["sql"])
    if not res.ok:
        return {"execution_error": f"{res.stage}: {res.error}", "result_df": None,
                "_trace": {"ok": False, "stage": res.stage, "error": res.error}}
    return {"execution_error": None, "result_df": df_to_frame(res.df),
            "_trace": {"ok": True, "rows": len(res.df), "db_ms": res.elapsed_ms}}


def n_verify(state, trace):
    v = verifier.verify_frame(state.get("result_df"), state.get("plan"))
    retry = (not v["ok"]) and state.get("verify_retries", 0) < 1
    updates = {"verification": {**v, "retry": retry}, "_trace": {"ok": v["ok"], "retry": retry}}
    if retry:
        updates["verify_retries"] = state.get("verify_retries", 0) + 1
    return updates


def n_analyze(state, trace):
    return {"analysis": analyze(state.get("result_df"), state.get("intent", "other"))}


def n_visualize(state, trace):
    spec = build_chart(state.get("result_df"), state.get("intent", "other"))
    return {"chart_spec": spec, "_trace": {"chart": bool(spec)}}


def n_respond(state, trace):
    return responder.run(state, trace)


def n_grounding(state, trace):
    payload = responder.build_payload(state)
    report = check_grounding(state["answer"], payload, state.get("analysis"),
                             extra_allowed=[payload["row_count"]])
    updates = {"grounded": report.grounded, "grounding_report": report.as_dict(),
               "_trace": report.as_dict()}
    final = state.get("answer_attempts", 1) >= 2
    if not report.grounded and final:
        updates["answer"] = (state["answer"] + "\n\n⚠️ Some figures in this answer could not be "
                             "verified against the query results; rely on the data table.")
    return updates


def n_fail(state, trace):
    errs = state.get("validation_errors") or ([state["execution_error"]] if state.get("execution_error") else [])
    return {
        "answer": ("I couldn't produce a valid, safe query for this question after "
                   f"{state.get('sql_attempts', 0)} attempts, so I won't guess an answer. "
                   "Please rephrase or narrow the question."),
        "result_df": None, "grounded": True, "chart_spec": None, "analysis": {},
        "_trace": {"errors": errs},
    }


def n_clarify_answer(state, trace):
    return {"answer": state["clarification_question"], "grounded": True,
            "result_df": None, "chart_spec": None}


# ----------------------------------------------------------------------------- routing

def _after_clarify(state) -> str:
    return "clarified" if state.get("needs_clarification") else "plan"


def _after_validate(state) -> str:
    if not state.get("validation_errors"):
        return "execute"
    return "generate_sql" if state.get("sql_attempts", 0) < get_settings().max_sql_attempts else "fail"


def _after_execute(state) -> str:
    if not state.get("execution_error"):
        return "verify"
    return "generate_sql" if state.get("sql_attempts", 0) < get_settings().max_sql_attempts else "fail"


def _after_verify(state) -> str:
    return "generate_sql" if (state.get("verification") or {}).get("retry") else "analyze"


def _after_grounding(state) -> str:
    if state.get("grounded") or state.get("answer_attempts", 1) >= 2:
        return END
    return "respond"


@lru_cache
def build_graph():
    g = StateGraph(AgentState)
    nodes = {
        "understand": n_understand, "retrieve": n_retrieve, "clarify": n_clarify, "plan": n_plan,
        "generate_sql": n_generate_sql, "validate": n_validate, "execute": n_execute,
        "verify": n_verify, "analyze": n_analyze, "visualize": n_visualize, "respond": n_respond,
        "grounding_check": n_grounding, "fail": n_fail, "clarified": n_clarify_answer,
    }
    for name, fn in nodes.items():
        g.add_node(name, _node(name, fn))
    g.add_edge(START, "understand")
    g.add_edge("understand", "retrieve")
    g.add_edge("retrieve", "clarify")
    g.add_conditional_edges("clarify", _after_clarify, {"clarified": "clarified", "plan": "plan"})
    g.add_edge("clarified", END)
    g.add_edge("plan", "generate_sql")
    g.add_edge("generate_sql", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"execute": "execute", "generate_sql": "generate_sql", "fail": "fail"})
    g.add_conditional_edges("execute", _after_execute,
                            {"verify": "verify", "generate_sql": "generate_sql", "fail": "fail"})
    g.add_conditional_edges("verify", _after_verify,
                            {"generate_sql": "generate_sql", "analyze": "analyze"})
    g.add_edge("analyze", "visualize")
    g.add_edge("visualize", "respond")
    g.add_edge("respond", "grounding_check")
    g.add_conditional_edges("grounding_check", _after_grounding, {"respond": "respond", END: END})
    g.add_edge("fail", END)
    return g.compile()


def run_agent(question: str) -> dict:
    """Run the full pipeline; returns the final state plus total latency."""
    started = time.perf_counter()
    state: AgentState = {"question": question.strip(), "trace": [], "timings": {},
                         "sql_attempts": 0, "verify_retries": 0, "answer_attempts": 0}
    try:
        final = build_graph().invoke(state, {"recursion_limit": 40})
        final["error"] = None
    except Exception as exc:  # LLM/DB outage etc.: surface, never fabricate
        final = {**state, "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                 "answer": "The analysis could not be completed due to a service error.",
                 "grounded": False}
    final["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return final
