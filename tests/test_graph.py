"""Graph tests with a scripted fake LLM against the real (seeded) database."""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import text

from agents.executor import get_engine

GOOD_SQL = """SELECT p.category,
 SUM(i.line_revenue) FILTER (WHERE o.order_date BETWEEN '2025-10-01' AND '2025-12-31') AS prev_revenue,
 SUM(i.line_revenue) FILTER (WHERE o.order_date BETWEEN '2026-01-01' AND '2026-03-31') AS curr_revenue
FROM analytics.fact_orders o JOIN analytics.fact_order_items i ON i.order_id = o.order_id
JOIN analytics.dim_product p ON p.product_id = i.product_id
JOIN analytics.dim_customer c ON c.customer_id = o.customer_id
JOIN analytics.dim_region r ON r.region_id = c.region_id
WHERE o.status = 'Completed' AND r.region_name = 'South India'
AND o.order_date BETWEEN '2025-10-01' AND '2026-03-31' GROUP BY p.category"""

UNDERSTAND = {"intent": "diagnostic", "entities": ["South India"], "metrics": ["revenue"],
              "filters": {"region": "South India"},
              "time_range": {"kind": "this_quarter", "compare_to": "previous_period"}}
PLAN = {"steps": ["compare"], "tables": ["fact_orders"], "compare_periods": True,
        "output_columns": ["category", "prev_revenue", "curr_revenue"]}


@pytest.fixture(autouse=True)
def _db():
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("database not reachable")


def install(monkeypatch, *, sqls, answers, understand=UNDERSTAND, clarify=None):
    sqls, answers = list(sqls), list(answers)
    calls = {"clarifier": 0}

    def fake_for_node(node, json_mode=False):
        def run(messages):
            user = messages[-1].content
            if node == "understand":
                body = json.dumps(understand)
            elif node == "clarifier":
                calls["clarifier"] += 1
                body = json.dumps(clarify)
            elif node == "planner":
                body = json.dumps(PLAN)
            elif node == "sql_generator":
                body = json.dumps({"sql": sqls.pop(0) if len(sqls) > 1 else sqls[0]})
            else:  # responder: build text from the structured payload it was given
                payload = json.loads(user.split("Data:\n", 1)[1].split("\n\nYour previous")[0])
                body = answers.pop(0)(payload) if len(answers) > 1 else answers[0](payload)
            return AIMessage(body, response_metadata={"model_name": f"fake/{node}"})
        return RunnableLambda(run)

    monkeypatch.setattr("app.llm.get_llm_for_node", fake_for_node)
    return calls


def good_answer(payload):
    top = max(payload["rows"], key=lambda r: abs(r["curr_revenue"] - r["prev_revenue"]))
    return (f"South India revenue fell. Biggest mover: {top['category']} "
            f"({top['prev_revenue']:.2f} to {top['curr_revenue']:.2f}).")


def test_happy_path_diagnostic(monkeypatch):
    from app.graph import run_agent
    install(monkeypatch, sqls=[GOOD_SQL], answers=[good_answer])
    out = run_agent("Why did South India revenue drop last quarter?")
    assert out["error"] is None and out["grounded"] is True
    assert out["time_range"]["start"] == "2026-01-01"
    assert out["sql"].upper().rstrip().endswith("LIMIT 1000")
    assert "drivers" in out["analysis"] and out["chart_spec"]
    assert {e["node"] for e in out["trace"]} >= {"understand", "validate", "execute", "respond"}
    llm_models = [e["model"] for e in out["trace"] if e["event"] == "llm_call"]
    assert llm_models and all(m.startswith("fake/") for m in llm_models)


def test_validator_rejection_then_repair(monkeypatch):
    from app.graph import run_agent
    install(monkeypatch, sqls=["DELETE FROM analytics.dim_region", GOOD_SQL], answers=[good_answer])
    out = run_agent("Why did South India revenue drop last quarter?")
    assert out["sql_attempts"] == 2 and out["grounded"] and out["result_df"]


def test_gives_up_after_max_attempts(monkeypatch):
    from app.graph import run_agent
    install(monkeypatch, sqls=["DROP TABLE analytics.dim_region"], answers=[good_answer])
    out = run_agent("Why did South India revenue drop last quarter?")
    assert out["sql_attempts"] == 3 and out["result_df"] is None
    assert "won't guess" in out["answer"]


def test_ungrounded_answer_regenerated_once(monkeypatch):
    from app.graph import run_agent
    install(monkeypatch, sqls=[GOOD_SQL],
            answers=[lambda p: "Revenue fell by 4242.37% to ₹7771.3 crore.", good_answer])
    out = run_agent("Why did South India revenue drop last quarter?")
    assert out["answer_attempts"] == 2 and out["grounded"] is True


def test_ungrounded_twice_flags_warning(monkeypatch):
    from app.graph import run_agent
    install(monkeypatch, sqls=[GOOD_SQL], answers=[lambda p: "Revenue fell by 4242.37%."])
    out = run_agent("Why did South India revenue drop last quarter?")
    assert out["grounded"] is False and "could not be verified" in out["answer"]


def test_clarification_short_circuits(monkeypatch):
    from app.graph import run_agent
    und = {**UNDERSTAND, "unknown_terms": ["customer lifetime value"], "out_of_scope": True}
    calls = install(monkeypatch, sqls=[GOOD_SQL], answers=[good_answer], understand=und,
                    clarify={"needs_clarification": True,
                             "clarification_question": "CLV is not defined in the data. Do you want revenue per customer?"})
    out = run_agent("What is customer lifetime value?")
    assert out["needs_clarification"] and out["sql"] is None if "sql" in out else True
    assert "revenue per customer" in out["answer"] and calls["clarifier"] == 1


def test_empty_result_retries_once(monkeypatch):
    from app.graph import run_agent
    empty = GOOD_SQL.replace("'South India'", "'Atlantis'")
    install(monkeypatch, sqls=[empty, GOOD_SQL], answers=[good_answer])
    out = run_agent("Why did South India revenue drop last quarter?")
    assert out["verify_retries"] == 1 and out["result_df"]["records"]
