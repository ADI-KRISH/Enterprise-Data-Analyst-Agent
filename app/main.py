"""FastAPI service: POST /analyze, GET /health, GET /schema (CLAUDE.md "API contract")."""

from __future__ import annotations

import asyncio
import hmac
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.output_guard import GENERIC_ERROR
from app.audit import audit, new_request_id
from app.config import get_settings
from app.graph import run_agent
from database.allowlist import load_allowlist

app = FastAPI(title="Enterprise Data Analyst Agent")


class AnalyzeRequest(BaseModel):
    # Hard bound for schema docs; the configured MAX_QUESTION_CHARS is enforced below (HTTP 422).
    question: str = Field(..., min_length=1)


def _error(status: int, request_id: str, message: str = GENERIC_ERROR) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, "request_id": request_id})


# ------------------------------------------------------------------------------ auth

def require_api_key(request: Request) -> None:
    expected = get_settings().api_key
    provided = request.headers.get("x-api-key", "")
    if expected is None or not hmac.compare_digest(provided.encode(), expected.get_secret_value().encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ------------------------------------------------------------------------ rate limit

class RateLimiter:
    """In-memory sliding window per client IP."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str, limit: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[client]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    if not limiter.allow(client, get_settings().rate_limit_per_min):
        raise HTTPException(status_code=429, detail="Too many requests")


# ------------------------------------------------------------------------- handlers

@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    rid = new_request_id()
    audit("http_error", rid, status=exc.status_code, route=request.url.path)
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail), "request_id": rid})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    rid = new_request_id()
    audit("http_error", rid, status=422, route=request.url.path)  # details stay out of the response
    return _error(422, rid, "Invalid request.")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    rid = new_request_id()
    audit("unhandled_error", rid, route=request.url.path, error=f"{type(exc).__name__}: {exc}")
    return _error(500, rid)


# --------------------------------------------------------------------------- routes

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schema", dependencies=[Depends(require_api_key)])
def schema() -> dict[str, Any]:
    al = load_allowlist()
    return {
        "schema": al.schema,
        "tables": [
            {"name": t.name, "description": t.description,
             "columns": list(t.columns), "restricted": sorted(t.restricted)}
            for t in al.tables.values()
        ],
    }


@app.post("/analyze", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def analyze(body: AnalyzeRequest) -> JSONResponse:
    s = get_settings()
    rid = new_request_id()
    if len(body.question) > s.max_question_chars:
        audit("http_error", rid, status=422, route="/analyze", reason="question_too_long")
        return _error(422, rid, f"question must be at most {s.max_question_chars} characters")
    try:
        response = await asyncio.wait_for(asyncio.to_thread(run_agent, body.question, rid),
                                          timeout=s.request_timeout_s)
    except asyncio.TimeoutError:
        audit("request_timeout", rid, question=body.question)
        return _error(504, rid)
    except Exception as exc:  # noqa: BLE001 - full error to the audit log only
        audit("request_error", rid, question=body.question, error=f"{type(exc).__name__}: {exc}")
        return _error(500, rid)
    if response.get("error"):
        return _error(500, rid)
    return JSONResponse(content=jsonable_encoder(response))
