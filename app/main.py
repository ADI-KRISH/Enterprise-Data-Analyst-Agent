"""FastAPI service: POST /analyze, GET /health, GET /schema."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from agents.executor import get_engine
from agents.sql_validator import load_allowlist
from app.audit import write_audit
from app.config import get_settings
from app.graph import run_agent

log = logging.getLogger("eda.api")
app = FastAPI(title="Enterprise Data Analyst Agent", version="1.0.0")


class AnalyzeRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)


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
    error: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok,
            "llm_configured": settings.openrouter_api_key is not None}


@app.get("/schema")
def schema() -> dict[str, Any]:
    return {"schema": "analytics", "tables": load_allowlist()}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest) -> AnalyzeResponse:
    started = time.perf_counter()
    try:
        state = run_agent(req.question)
    except Exception as exc:  # defensive: run_agent already converts node errors
        log.exception("agent crashed")
        raise HTTPException(status_code=500, detail="internal error") from exc
    latency_ms = round((time.perf_counter() - started) * 1000)

    result = state.get("result_df") or {}
    error = state.get("error")
    answer = state.get("answer") or (f"I couldn't complete this analysis. {error}" if error else "")
    resp = AnalyzeResponse(
        answer=answer,
        sql=state.get("sql"),
        data=result.get("records", []),
        columns=result.get("columns", []),
        visualization=state.get("chart_spec"),
        analysis=state.get("analysis") or {},
        grounded=bool(state.get("grounded")),
        needs_clarification=bool(state.get("needs_clarification")),
        clarification_question=state.get("clarification_question") or None,
        trace=state.get("trace", []),
        latency_ms=latency_ms,
        error=error,
    )
    validation = next((e for e in reversed(state.get("trace", [])) if e.get("event") == "validation"), None)
    write_audit(
        question=req.question, sql=resp.sql,
        validation={"ok": validation["ok"], "errors": validation["errors"]} if validation else None,
        row_count=len(resp.data) if result else None, latency_ms=latency_ms,
        grounded=resp.grounded if not resp.needs_clarification and not error else None,
        needs_clarification=resp.needs_clarification, error=error,
    )
    return resp
