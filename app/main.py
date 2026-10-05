"""FastAPI service: POST /analyze, GET /health, GET /schema."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from agents.executor import get_anchor_date, get_engine
from agents.sql_validator import load_allowlist
from agents.understand import kpi_names
from app.audit import write_audit
from app.config import get_settings
from app.graph import run_agent

app = FastAPI(title="Enterprise Data Analyst Agent", version="0.1.0")


class AnalyzeRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AnalyzeResponse(BaseModel):
    answer: str
    sql: str | None
    data: list[dict[str, Any]]
    columns: list[str]
    visualization: dict[str, Any] | None
    analysis: dict[str, Any]
    grounded: bool
    needs_clarification: bool
    clarification_question: str | None
    trace: list[dict[str, Any]]
    latency_ms: int


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest) -> AnalyzeResponse:
    final = run_agent(req.question)
    frame = final.get("result_df") or {"columns": [], "records": []}
    validation = next((e for e in reversed(final.get("trace", []))
                       if e.get("node") == "validate"), {})
    write_audit({
        "question": req.question, "sql": final.get("sql"),
        "validation": {k: validation.get(k) for k in ("ok", "errors", "limit_added")},
        "row_count": len(frame["records"]), "latency_ms": final["latency_ms"],
        "grounded": final.get("grounded"), "needs_clarification": final.get("needs_clarification", False),
        "error": final.get("error"),
    })
    if final.get("error"):
        raise HTTPException(status_code=502, detail=final["error"])
    return AnalyzeResponse(
        answer=final.get("answer", ""), sql=final.get("sql"), data=frame["records"],
        columns=frame["columns"], visualization=final.get("chart_spec"),
        analysis=final.get("analysis") or {}, grounded=bool(final.get("grounded")),
        needs_clarification=bool(final.get("needs_clarification")),
        clarification_question=final.get("clarification_question"),
        trace=final.get("trace", []), latency_ms=final["latency_ms"],
    )


@app.get("/health")
def health() -> dict:
    status: dict[str, Any] = {"status": "ok"}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        status["database"] = "ok"
        status["anchor_date"] = get_anchor_date().isoformat()
    except Exception as exc:
        status.update(status="degraded", database=f"error: {type(exc).__name__}")
    status["llm_configured"] = get_settings().openrouter_api_key is not None
    return status


@app.get("/schema")
def schema() -> dict:
    allow = load_allowlist()
    return {"schema": allow["schema"],
            "tables": {t: sorted(c) for t, c in allow["tables"].items()},
            "kpis": kpi_names()}
