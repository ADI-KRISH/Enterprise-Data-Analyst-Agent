"""Eval harness tests: question file shape and result matching (no LLM)."""

from __future__ import annotations

from collections import Counter

import yaml

from agents.input_guard import rule_category
from eval.run_eval import QUESTIONS, results_match, summarize

CASES = yaml.safe_load(QUESTIONS.read_text())["questions"]


def test_guardrail_cases_cover_every_category_twice():
    reasons = Counter(c["expected_reason"] for c in CASES if c["expected"] == "refuse")
    assert sum(reasons.values()) >= 8
    for r in ("off_topic", "unsafe_write", "prompt_injection", "pii_request"):
        assert reasons[r] >= 2


def test_borderline_analytics_not_caught_by_keyword_rules():
    for c in CASES:
        if c["expected"] == "answer":
            assert rule_category(c["question"]) is None, c["id"]


def test_rule_categories_agree_with_expected_when_a_rule_fires():
    for c in CASES:
        if c["expected"] == "refuse" and rule_category(c["question"]):
            assert rule_category(c["question"]) == c["expected_reason"], c["id"]


def test_ids_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_gold_sql_validates_and_is_under_cost_cap(executor):
    from app.config import get_settings

    for c in CASES:
        if c.get("gold_sql"):
            r = executor.run(c["gold_sql"])
            assert r.ok, (c["id"], r.error_code)
            assert r.cost < get_settings().max_query_cost


def test_results_match():
    gold = [{"region": "East India", "revenue": 100.0}]
    assert results_match(gold, [{"region_name": "east india", "total": 100.05, "x": 1}])
    assert not results_match(gold, [{"region": "East India", "revenue": 150.0}])
    assert not results_match(gold, [])


def test_summarize_rates():
    rows = [
        {"id": "g", "expected": "refuse", "blocked": True, "reason_ok": False, "latency_ms": 10},
        {"id": "a", "expected": "answer", "false_refusal": True, "executed": False, "error": False,
         "grounded": None, "latency_ms": 20},
    ]
    s = summarize(rows)
    assert s["guardrail"]["block_rate_pct"] == 100.0 and s["guardrail"]["reason_accuracy_pct"] == 0.0
    assert s["analytics"]["false_refusal_rate_pct"] == 100.0
