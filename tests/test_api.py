import json

from fastapi.testclient import TestClient

from app import audit, main

client = TestClient(main.app)

FINAL = {
    "answer": "ok", "sql": "SELECT 1", "result_df": {"columns": ["a"], "records": [{"a": 1}]},
    "chart_spec": None, "analysis": {}, "grounded": True, "needs_clarification": False,
    "clarification_question": None, "trace": [{"node": "validate", "ok": True, "errors": []}],
    "latency_ms": 12, "error": None,
}


def test_analyze_contract_and_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "run_agent", lambda q: dict(FINAL))
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    r = client.post("/analyze", json={"question": "revenue by region?"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"answer", "sql", "data", "columns", "visualization", "analysis", "grounded",
                         "needs_clarification", "clarification_question", "trace", "latency_ms"}
    line = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[0])
    assert line["question"] == "revenue by region?" and line["row_count"] == 1 and line["grounded"]


def test_service_error_is_502(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "run_agent", lambda q: {**FINAL, "error": "RuntimeError: boom"})
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    assert client.post("/analyze", json={"question": "revenue by region?"}).status_code == 502


def test_validation_and_schema():
    assert client.post("/analyze", json={"question": "x"}).status_code == 422
    body = client.get("/schema").json()
    assert "fact_orders" in body["tables"] and "Revenue (a.k.a. sales, net sales)" in body["kpis"]
    assert client.get("/health").status_code == 200
