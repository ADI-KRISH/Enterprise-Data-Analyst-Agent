"""Pipeline tests with scripted fake LLMs (no network). Need the seeded database."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agents import clarifier, planner, responder, sql_generator, understand
from app import audit, graph, main

REVENUE_BY_REGION = (
    "SELECT r.region_name, SUM(i.line_revenue) AS revenue FROM fact_orders o "
    "JOIN fact_order_items i ON i.order_id = o.order_id JOIN dim_customer c ON c.customer_id = o.customer_id "
    "JOIN dim_region r ON r.region_id = c.region_id WHERE o.status = 'Completed' GROUP BY r.region_name ORDER BY revenue DESC"
)
EMPTY = "SELECT region_name FROM dim_region WHERE region_name = 'Nowhere'"
UNDERSTANDING = {"intent": "breakdown", "metrics": ["revenue"], "entities": {}, "filters": [],
                 "time_phrase": "last quarter", "time_start": None, "time_end": None, "ambiguities": []}


class Script:
    """Per-node queues of canned LLM outputs; the last item repeats."""

    def __init__(self, **queues):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls: dict[str, int] = {}

    def llm_for(self, node, json_mode=False):
        def run(_messages):
            self.calls[node] = self.calls.get(node, 0) + 1
            q = self.queues[node]
            item = q.pop(0) if len(q) > 1 else q[0]
            return AIMessage(item if isinstance(item, str) else json.dumps(item),
                             response_metadata={"model_name": "fake/model"})
        return RunnableLambda(run)


@pytest.fixture
def script(monkeypatch, db):
    def install(**queues):
        defaults = {
            "understand": [UNDERSTANDING],
            "clarifier": [{"needs_clarification": False}],
            "planner": [{"approach": "group revenue by region"}],
            "sql_generator": [{"sql": REVENUE_BY_REGION}],
            "responder": ["South India and West India are shown in the table."],
        }
        s = Script(**{**defaults, **queues})
        for mod in (understand, clarifier, planner, sql_generator, responder):
            monkeypatch.setattr(mod, "get_llm_for_node", s.llm_for)
        monkeypatch.setattr(graph, "retrieve", lambda q: {"schema": "s", "chunks": [], "backend": "stub"})
        return s
    return install


def test_happy_path(script):
    s = script()
    state = graph.run_agent("Show revenue by region")
    assert not state.get("error"), state.get("error")
    assert state["grounded"] is True
    assert state["sql_attempts"] == 1 and "LIMIT" in state["sql"]
    assert len(state["result_df"]["records"]) == 5
    assert state["chart_spec"] is not None and state["analysis"]["row_count"] == 5
    assert state["time_range"]["start"] == "2026-07-01"          # anchor-date resolution, not the system clock
    assert {"understand", "plan", "execute", "respond", "total"} <= set(state["timings"])
    assert all(e["model"] == "fake/model" for e in state["trace"] if e["event"] == "llm_call")
    assert s.calls["responder"] == 1


def test_validation_loop_retries_with_errors(script):
    s = script(sql_generator=["not json at all {", {"sql": "DROP TABLE fact_orders"}, {"sql": REVENUE_BY_REGION}])
    state = graph.run_agent("Show revenue by region")
    assert not state.get("error"), state.get("error")
    assert state["sql_attempts"] == 2
    events = [e for e in state["trace"] if e["event"] == "validation"]
    assert [e["ok"] for e in events] == [False, True]


def test_gives_up_after_max_attempts(script):
    script(sql_generator=[{"sql": "DELETE FROM fact_orders"}])
    state = graph.run_agent("Delete everything")
    assert state["error"].startswith("Could not produce a valid")
    assert state["sql_attempts"] == 3 and not state.get("result_df")


def test_database_error_goes_back_to_generator(script):
    bad_runtime = "SELECT 1 / 0 AS x FROM dim_region"          # passes validation, fails at execution
    script(sql_generator=[{"sql": bad_runtime}, {"sql": REVENUE_BY_REGION}])
    state = graph.run_agent("Show revenue by region")
    assert not state.get("error") and state["sql_attempts"] == 2
    assert any(e["event"] == "db_error" for e in state["trace"])


def test_empty_result_triggers_one_verify_retry(script):
    script(sql_generator=[{"sql": EMPTY}, {"sql": REVENUE_BY_REGION}])
    state = graph.run_agent("Show revenue by region")
    assert state["verify_retried"] is True and len(state["result_df"]["records"]) == 5


def test_persistently_empty_result_still_answers(script):
    script(sql_generator=[{"sql": EMPTY}], responder=["No matching data was found."])
    state = graph.run_agent("Show revenue for Nowhere")
    assert not state.get("error") and state["result_df"]["records"] == []
    assert state["chart_spec"] is None and state["verification"]["issues"]


def test_clarification_stops_before_sql(script):
    s = script(clarifier=[{"needs_clarification": True, "question": "Which churn definition do you mean?"}])
    state = graph.run_agent("What is our churn rate?")
    assert state["needs_clarification"] and state["answer"] == "Which churn definition do you mean?"
    assert "sql_generator" not in s.calls and not state.get("sql")


def test_ungrounded_answer_regenerated_once_then_flagged(script):
    s = script(responder=["Revenue was ₹9,999 crore."])
    state = graph.run_agent("Show revenue by region")
    assert s.calls["responder"] == 2
    assert state["grounded"] is False and "₹9,999 crore" in state["grounding_issues"]


def test_regeneration_can_fix_grounding(script):
    s = script(responder=["Revenue was ₹9,999 crore.", "South India and West India lead."])
    state = graph.run_agent("Show revenue by region")
    assert s.calls["responder"] == 2 and state["grounded"] is True


def test_llm_failure_is_reported_not_raised(script, monkeypatch):
    script()

    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(understand, "invoke_structured", boom)
    state = graph.run_agent("Show revenue by region")
    assert "provider down" in state["error"]


def test_api_contract_and_audit_log(script, tmp_path, monkeypatch):
    script()
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    client = TestClient(main.app)
    r = client.post("/analyze", json={"question": "Show revenue by region"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"answer", "sql", "data", "columns", "visualization", "analysis", "grounded",
                         "needs_clarification", "clarification_question", "trace", "latency_ms"}
    assert body["columns"] == ["region_name", "revenue"] and len(body["data"]) == 5
    line = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert line["question"] == "Show revenue by region" and line["row_count"] == 5
    assert line["validation"]["ok"] is True and line["grounded"] is True

    assert client.post("/analyze", json={"question": "x"}).status_code == 422
    assert client.get("/health").json()["database"] is True
    assert "fact_orders" in client.get("/schema").json()["tables"]
