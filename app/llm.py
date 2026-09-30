"""LLM access via OpenRouter (OpenAI-compatible API).

- get_llm(kind) -> ChatOpenAI (temperature 0, 60s timeout, low reasoning effort,
  reasoning excluded from content), retried on 429 with exponential backoff,
  then falling back to LLM_MODEL_FALLBACK.
- invoke_text / invoke_structured record which model actually answered into a
  trace list (state["trace"]).
- Structured output = JSON mode + Pydantic validation, with one repair retry.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, TypeVar

import openai
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
RATE_LIMIT_RETRIES = 3  # retries after the first attempt, on HTTP 429 only
BACKOFF_PARAMS = {"initial": 2.0, "max": 30.0}  # seconds, exponential with jitter

LLMKind = Literal["strong", "fast"]

# Which model tier each graph node uses.
NODE_LLM_KIND: dict[str, LLMKind] = {
    "planner": "strong",
    "sql_generator": "strong",
    "understand": "fast",
    "clarifier": "fast",
    "responder": "fast",
}

# OpenRouter unified reasoning control: keep thinking short and never return it
# in message content (it would break JSON parsing).
_REASONING_BODY = {"reasoning": {"effort": "low", "exclude": True}}
_JSON_MODE = {"type": "json_object"}

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """LLM output could not be parsed into the schema, even after one repair."""


def _chat_model(model: str, settings: Settings) -> ChatOpenAI:
    if settings.openrouter_api_key is None:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return ChatOpenAI(
        model=model,
        base_url=OPENROUTER_BASE_URL,
        api_key=settings.openrouter_api_key,
        temperature=0,
        timeout=settings.llm_timeout_s,
        max_retries=0,  # 429 retries handled by with_retry below; no double retrying
        extra_body=_REASONING_BODY,
    )


def _with_rate_limit_retry(runnable: Runnable) -> Runnable:
    return runnable.with_retry(
        retry_if_exception_type=(openai.RateLimitError,),
        wait_exponential_jitter=True,
        exponential_jitter_params=BACKOFF_PARAMS,
        stop_after_attempt=1 + RATE_LIMIT_RETRIES,
    )


def get_llm(kind: LLMKind, *, json_mode: bool = False) -> Runnable:
    """Primary model for `kind`, with 429 backoff, falling back to LLM_MODEL_FALLBACK."""
    settings = get_settings()
    primary = {"strong": settings.llm_model_strong, "fast": settings.llm_model_fast}[kind]

    def build(model: str) -> Runnable:
        chat: Runnable = _chat_model(model, settings)
        if json_mode:
            chat = chat.bind(response_format=_JSON_MODE)
        return _with_rate_limit_retry(chat)

    return build(primary).with_fallbacks([build(settings.llm_model_fallback)])


def get_llm_for_node(node: str, *, json_mode: bool = False) -> Runnable:
    return get_llm(NODE_LLM_KIND[node], json_mode=json_mode)


# --------------------------------------------------------------------------- helpers

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def clean_content(content: Any) -> str:
    """Message content as plain text with any leaked <think> blocks removed."""
    if isinstance(content, list):  # content blocks
        content = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return _THINK_RE.sub("", str(content)).strip()


def extract_json(text: str) -> Any:
    """Parse a JSON object from model text, tolerating code fences and surrounding prose."""
    text = _FENCE_RE.sub("", clean_content(text).strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def answered_by(message: AIMessage) -> str | None:
    """Model that actually produced the message (OpenRouter echoes it in `model`)."""
    meta = message.response_metadata or {}
    return meta.get("model_name") or meta.get("model")


def _record(
    trace: list[dict] | None, node: str, message: AIMessage | None, started: float, **extra: Any
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "node": node,
            "event": "llm_call",
            "model": answered_by(message) if message is not None else None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            **extra,
        }
    )


# --------------------------------------------------------------------------- invoke

def invoke_text(
    llm: Runnable, messages: list[BaseMessage], *, node: str, trace: list[dict] | None = None
) -> str:
    started = time.perf_counter()
    message: AIMessage = llm.invoke(messages)
    _record(trace, node, message, started)
    return clean_content(message.content)


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object only — no prose, no code fences. "
        "It must validate against this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )


def invoke_structured(
    llm: Runnable,
    messages: list[BaseMessage],
    schema: type[T],
    *,
    node: str,
    trace: list[dict] | None = None,
) -> T:
    """JSON-mode call validated by `schema`; on parse/validation failure, one repair retry.

    `llm` should come from get_llm(..., json_mode=True).
    """
    convo = [SystemMessage(_schema_instruction(schema)), *messages]
    last_error: Exception | None = None
    for attempt in (1, 2):
        started = time.perf_counter()
        message: AIMessage = llm.invoke(convo)
        try:
            result = schema.model_validate(extract_json(message.content))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            _record(trace, node, message, started, attempt=attempt, parsed=False)
            convo = [
                *convo,
                AIMessage(clean_content(message.content)),
                HumanMessage(
                    "That output was not valid for the schema. Error:\n"
                    f"{str(exc)[:2000]}\n"
                    "Return the corrected JSON object only."
                ),
            ]
            continue
        _record(trace, node, message, started, attempt=attempt, parsed=True)
        return result
    raise StructuredOutputError(f"{node}: invalid structured output after repair: {last_error}")
