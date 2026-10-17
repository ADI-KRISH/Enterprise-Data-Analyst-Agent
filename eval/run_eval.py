"""Evaluation runner: python -m eval.run_eval [--ids a01,g03] [--limit N]

Runs every case in eval/questions.yaml through the full agent and writes
eval/results/<timestamp>.json. Reported separately (CLAUDE.md Guardrails §4):
- guardrail block rate (+ reason-code accuracy) over `expected: refuse` cases;
- false-refusal rate: analytics cases refused or output-blocked / analytics cases;
- for analytics cases with `gold_sql`: execution rate, result match against the gold result
  (gold SQL runs through the same validator/executor), grounded rate, latency.
Figures are only quotable from the saved results file (CLAUDE.md rule 5).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agents.executor import get_executor
from app.config import PROJECT_ROOT
from app.graph import run_agent

QUESTIONS = Path(__file__).resolve().parent / "questions.yaml"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
REL_TOL = 1e-3


def _numbers(rows: list[dict[str, Any]]) -> list[float]:
    return [float(v) for r in rows for v in r.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(float(v))]


def _labels(rows: list[dict[str, Any]]) -> set[str]:
    return {str(v).strip().lower()[:10] if str(v)[:4].isdigit() and "T" in str(v) else str(v).strip().lower()
            for r in rows for v in r.values() if isinstance(v, str)}


def results_match(gold: list[dict[str, Any]], got_rows: list[dict[str, Any]]) -> bool:
    """Every gold number and label appears in the agent's result (numbers within REL_TOL)."""
    if not gold:
        return not got_rows
    got_nums = _numbers(got_rows)
    for g in _numbers(gold):
        if not any(math.isclose(g, x, rel_tol=REL_TOL, abs_tol=1e-6) for x in got_nums):
            return False
    return _labels(gold) <= _labels(got_rows)


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    resp = run_agent(case["question"])
    refused_or_blocked = bool(resp.get("refused") or resp.get("output_blocked"))
    out: dict[str, Any] = {
        "id": case["id"], "question": case["question"], "expected": case["expected"],
        "refused": bool(resp.get("refused")), "refusal_reason": resp.get("refusal_reason"),
        "output_blocked": resp.get("output_blocked"), "needs_clarification": resp.get("needs_clarification"),
        "error": bool(resp.get("error")), "grounded": resp.get("grounded"),
        "latency_ms": resp.get("latency_ms"), "sql": resp.get("sql"), "request_id": resp.get("request_id"),
    }
    if case["expected"] == "refuse":
        out["blocked"] = refused_or_blocked
        out["reason_ok"] = resp.get("refusal_reason") == case.get("expected_reason")
    else:
        out["false_refusal"] = refused_or_blocked
        out["executed"] = bool(resp.get("queries"))
        if case.get("gold_sql"):
            gold = get_executor().run(case["gold_sql"])
            out["gold_ok"] = gold.ok
            out["match"] = bool(gold.ok and out["executed"] and results_match(gold.records, resp.get("data", [])))
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    guard = [r for r in rows if r["expected"] == "refuse"]
    analytics = [r for r in rows if r["expected"] == "answer"]
    gold = [r for r in analytics if "match" in r]
    lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    graded = [r for r in analytics if r.get("executed")]
    return {
        "cases": len(rows),
        "guardrail": {
            "cases": len(guard),
            "block_rate_pct": _pct(sum(r["blocked"] for r in guard), len(guard)),
            "reason_accuracy_pct": _pct(sum(r["reason_ok"] for r in guard), len(guard)),
        },
        "analytics": {
            "cases": len(analytics),
            "false_refusal_rate_pct": _pct(sum(r["false_refusal"] for r in analytics), len(analytics)),
            "execution_rate_pct": _pct(sum(r["executed"] for r in analytics), len(analytics)),
            "gold_cases": len(gold),
            "result_match_rate_pct": _pct(sum(r["match"] for r in gold), len(gold)),
            "grounded_rate_pct": _pct(sum(bool(r["grounded"]) for r in graded), len(graded)),
            "internal_error_rate_pct": _pct(sum(r["error"] for r in analytics), len(analytics)),
        },
        "latency_ms": {"p50": statistics.median(lat) if lat else None,
                       "p95": lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)] if lat else None},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.run_eval")
    ap.add_argument("--ids", help="comma-separated case ids")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    cases = yaml.safe_load(QUESTIONS.read_text())["questions"]
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    rows = []
    for c in cases:
        r = evaluate_case(c)
        rows.append(r)
        flag = r.get("blocked", not r.get("false_refusal"))
        print(f"{r['id']:<5} {'ok ' if flag else 'BAD'} {r['question'][:70]}", flush=True)
    summary = summarize(rows)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{stamp}.json"
    path.write_text(json.dumps({"timestamp": stamp, "summary": summary, "cases": rows}, indent=2,
                               default=str, ensure_ascii=False))
    print(json.dumps(summary, indent=2))
    print(f"saved {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
