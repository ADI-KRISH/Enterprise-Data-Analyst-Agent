"""Offline tests for app.llm (no network calls)."""

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from app import llm as llm_mod
from app.llm import (
    StructuredOutputError,
    clean_content,
    extract_json,
    get_llm,
    invoke_structured,
    invoke_text,
)


class Plan(BaseModel):
    steps: list[str]
    metric: str


def fake_llm(*contents: str, model: str = "vendor/model-x"):
    """Runnable returning the given contents in order, tagged with a model name."""
    queue = list(contents)
    calls: list = []

    def _run(messages):
        calls.append(messages)
        return AIMessage(queue.pop(0), response_metadata={"model_name": model})

    runnable = RunnableLambda(_run)
    runnable.calls = calls  # type: ignore[attr-defined]
    return runnable


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_RO", "postgresql+psycopg://ro:x@localhost/db")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    llm_mod.get_settings.cache_clear()
    yield
    llm_mod.get_settings.cache_clear()


def test_clean_content_strips_think_blocks():
    assert clean_content("<think>hmm {x}</think>\n{\"a\": 1}") == '{"a": 1}'


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! Here it is: {"a": 1} Hope that helps.'],
)
def test_extract_json_tolerates_wrappers(text):
    assert extract_json(text) == {"a": 1}


def test_invoke_text_records_answering_model():
    trace: list[dict] = []
    out = invoke_text(fake_llm("hello", model="nvidia/x"), [HumanMessage("hi")], node="responder", trace=trace)
    assert out == "hello"
    assert trace[0]["node"] == "responder" and trace[0]["model"] == "nvidia/x"


def test_structured_first_try():
    trace: list[dict] = []
    llm = fake_llm('{"steps": ["a"], "metric": "revenue"}')
    plan = invoke_structured(llm, [HumanMessage("q")], Plan, node="planner", trace=trace)
    assert plan == Plan(steps=["a"], metric="revenue")
    assert [e["parsed"] for e in trace] == [True]


def test_structured_repairs_once():
    trace: list[dict] = []
    llm = fake_llm('{"steps": "oops"}', '{"steps": ["a"], "metric": "aov"}')
    plan = invoke_structured(llm, [HumanMessage("q")], Plan, node="planner", trace=trace)
    assert plan.metric == "aov"
    assert [e["parsed"] for e in trace] == [False, True]
    repair_prompt = llm.calls[1][-1].content
    assert "not valid" in repair_prompt


def test_structured_gives_up_after_one_repair():
    llm = fake_llm("not json", "still not json")
    with pytest.raises(StructuredOutputError):
        invoke_structured(llm, [HumanMessage("q")], Plan, node="planner")


def _rate_limit_error() -> openai.RateLimitError:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.RateLimitError("429", response=httpx.Response(429, request=req), body=None)


def test_rate_limit_retried_with_backoff(monkeypatch):
    monkeypatch.setattr(llm_mod, "BACKOFF_PARAMS", {"initial": 0.01, "max": 0.02})
    attempts = {"n": 0}

    def flaky(_):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise _rate_limit_error()
        return AIMessage("ok")

    assert llm_mod._with_rate_limit_retry(RunnableLambda(flaky)).invoke([]).content == "ok"
    assert attempts["n"] == 4  # 1 + RATE_LIMIT_RETRIES


def test_non_rate_limit_errors_not_retried(monkeypatch):
    monkeypatch.setattr(llm_mod, "BACKOFF_PARAMS", {"initial": 0.01, "max": 0.02})
    attempts = {"n": 0}

    def boom(_):
        attempts["n"] += 1
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        llm_mod._with_rate_limit_retry(RunnableLambda(boom)).invoke([])
    assert attempts["n"] == 1


def _unwrap(runnable):
    """Walk binding/retry wrappers down to the ChatOpenAI; collect bound kwargs."""
    kwargs: dict = {}
    while hasattr(runnable, "bound"):
        kwargs.update(getattr(runnable, "kwargs", {}) or {})
        runnable = runnable.bound
    return runnable, kwargs


def test_get_llm_wiring(env):
    runnable = get_llm("strong", json_mode=True)
    primary, primary_kwargs = _unwrap(runnable.runnable)
    fallback, fallback_kwargs = _unwrap(runnable.fallbacks[0])
    assert primary.model_name == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert fallback.model_name == "nvidia/nemotron-3-super-120b-a12b:free"
    assert primary.openai_api_base == "https://openrouter.ai/api/v1"
    assert primary.temperature == 0 and primary.request_timeout == 60
    assert primary.max_retries == 0
    assert primary.extra_body == {"reasoning": {"effort": "low", "exclude": True}}
    assert primary_kwargs["response_format"] == fallback_kwargs["response_format"] == {"type": "json_object"}
    fast, fast_kwargs = _unwrap(get_llm("fast").runnable)
    assert fast.model_name == "qwen/qwen3.8-27b:free" and "response_format" not in fast_kwargs
