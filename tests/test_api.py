"""API tests (auth, rate limit, size cap, error hygiene). The agent is stubbed: no LLM, no DB."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import main
from app.config import get_settings

KEY = "test-api-key"


@pytest.fixture
def client(monkeypatch, audit_path):
    s = get_settings()
    monkeypatch.setattr(s, "api_key", SecretStr(KEY))
    monkeypatch.setattr(s, "rate_limit_per_min", 5)
    monkeypatch.setattr(s, "request_timeout_s", 2.0)
    main.limiter.reset()
    monkeypatch.setattr(main, "run_agent", lambda q, rid=None: {
        "answer": "ok", "request_id": rid, "error": None, "trace": [], "data": [], "columns": []})
    return TestClient(main.app)


def H(key=KEY):
    return {"X-API-Key": key}


def test_health_needs_no_key(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
def test_missing_or_wrong_key_is_401(client, headers):
    for r in (client.post("/analyze", json={"question": "x"}, headers=headers),
              client.get("/schema", headers=headers)):
        assert r.status_code == 401 and "request_id" in r.json()


def test_analyze_ok(client):
    r = client.post("/analyze", json={"question": "total revenue"}, headers=H())
    assert r.status_code == 200 and r.json()["answer"] == "ok"
    assert r.json()["request_id"]


def test_oversized_question_is_422(client):
    r = client.post("/analyze", json={"question": "a" * 501}, headers=H())
    assert r.status_code == 422


def test_empty_or_malformed_body_is_422(client):
    assert client.post("/analyze", json={"question": ""}, headers=H()).status_code == 422
    assert client.post("/analyze", json={}, headers=H()).status_code == 422


def test_rate_limit_429(client):
    codes = [client.post("/analyze", json={"question": "x"}, headers=H()).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_agent_exception_is_generic_and_audited(client, monkeypatch, audit_path):
    def boom(q, rid=None):
        raise RuntimeError("psycopg.errors.UndefinedTable: relation secret_table; password=hunter2")

    monkeypatch.setattr(main, "run_agent", boom)
    r = client.post("/analyze", json={"question": "x"}, headers=H())
    assert r.status_code == 500
    body = r.json()
    assert set(body) == {"error", "request_id"}
    assert "psycopg" not in r.text and "secret_table" not in r.text and "Traceback" not in r.text
    log = audit_path.read_text()
    assert body["request_id"] in log and "hunter2" not in log  # full error logged, redacted


def test_pipeline_error_response_is_generic(client, monkeypatch):
    monkeypatch.setattr(main, "run_agent", lambda q, rid=None: {"error": "Could not answer this question.",
                                                                  "request_id": rid})
    r = client.post("/analyze", json={"question": "x"}, headers=H())
    assert r.status_code == 500 and r.json()["error"] == "Could not answer this question."


def test_timeout_is_504(client, monkeypatch):
    import time

    monkeypatch.setattr(get_settings(), "request_timeout_s", 0.2)
    monkeypatch.setattr(main, "run_agent", lambda q, rid=None: time.sleep(1) or {})
    r = client.post("/analyze", json={"question": "x"}, headers=H())
    assert r.status_code == 504 and set(r.json()) == {"error", "request_id"}


def test_schema_lists_allowlisted_tables_only(client):
    body = client.get("/schema", headers=H()).json()
    names = {t["name"] for t in body["tables"]}
    assert "fact_orders" in names and "pg_catalog" not in json.dumps(body)
    cust = next(t for t in body["tables"] if t["name"] == "dim_customer")
    assert cust["restricted"] == ["name"]


def test_secrets_not_in_responses(client):
    r = client.post("/analyze", json={"question": "x"}, headers=H())
    assert KEY not in r.text
