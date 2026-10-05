"""Evaluation harness: runs eval/questions.yaml through the full agent and scores it against gold SQL.

    python -m eval.run_eval [--limit N] [--ids q01_revenue_last_quarter,c01_churn]

Writes eval/results/<timestamp>.json. Only numbers produced by this script (and saved there) may be quoted in the
README or docs (CLAUDE.md rule 5).

Scoring
  * execution_match: predicted result set equals the gold result set (every gold column has a matching predicted
    column with the same values, same number of rows; numerics compared to 0.5% / 0.01 tolerance).
  * clarification: for `clarify: true` questions the agent must ask instead of answering; for the others it
    must NOT ask (false clarification).
  * grounded: the answer's numbers were verified against the result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import time
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from agents.executor import payload_to_df, run_query
from app.config import PROJECT_ROOT, get_settings
from app.graph import run_agent

QUESTIONS_PATH = PROJECT_ROOT / "eval" / "questions.yaml"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"


def _col_values(s: pd.Series) -> list:
    if pd.api.types.is_numeric_dtype(s):
        return sorted(float(v) for v in s.dropna())
    return sorted(str(v).strip().lower() for v in s.dropna())


def _same(a: list, b: list) -> bool:
    if len(a) != len(b):
        return False
    if a and isinstance(a[0], float) and b and isinstance(b[0], float):
        return all(abs(x - y) <= max(0.01, 0.005 * abs(x)) for x, y in zip(a, b))
    return a == b


def results_match(gold: pd.DataFrame, pred: pd.DataFrame) -> bool:
    """Order-insensitive, column-name-insensitive comparison; extra predicted columns are allowed."""
    if len(gold) != len(pred) or pred.empty != gold.empty:
        return False
    used: set[str] = set()
    for gc in gold.columns:
        gv = _col_values(gold[gc])
        match = next((pc for pc in pred.columns if pc not in used and _same(gv, _col_values(pred[pc]))), None)
        if match is None:
            return False
        used.add(match)
    return True


def evaluate_one(item: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    state = run_agent(item["question"])
    latency_ms = round((time.perf_counter() - started) * 1000)
    rec: dict[str, Any] = {
        "id": item["id"], "type": item["type"], "question": item["question"], "latency_ms": latency_ms,
        "error": state.get("error"), "needs_clarification": bool(state.get("needs_clarification")),
        "sql": state.get("sql"), "grounded": bool(state.get("grounded")), "answer": state.get("answer"),
        "sql_attempts": state.get("sql_attempts"), "expected_clarification": bool(item.get("clarify")),
    }
    if item.get("clarify"):
        rec["pass"] = rec["needs_clarification"]
        return rec
    rec["false_clarification"] = rec["needs_clarification"]
    rec["valid_sql"] = bool(state.get("result_df"))
    if rec["valid_sql"]:
        gold = run_query(item["gold_sql"])
        rec["execution_match"] = results_match(gold, payload_to_df(state["result_df"]))
    else:
        rec["execution_match"] = False
    rec["pass"] = rec["execution_match"]
    return rec


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [r for r in results if not r["expected_clarification"]]
    clar = [r for r in results if r["expected_clarification"]]
    lat = sorted(r["latency_ms"] for r in results)

    def rate(xs: list[bool]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    return {
        "n_questions": len(results), "n_answerable": len(answerable), "n_clarify": len(clar),
        "execution_accuracy": rate([r["execution_match"] for r in answerable]),
        "valid_sql_rate": rate([r["valid_sql"] for r in answerable]),
        "grounded_rate": rate([r["grounded"] for r in answerable if r["valid_sql"] and not r["error"]]),
        "clarification_recall": rate([r["needs_clarification"] for r in clar]),
        "false_clarification_rate": rate([r["false_clarification"] for r in answerable]),
        "error_rate": rate([bool(r["error"]) for r in results]),
        "latency_ms": {"mean": round(statistics.mean(lat)) if lat else None,
                       "p50": lat[len(lat) // 2] if lat else None,
                       "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma-separated question ids")
    args = ap.parse_args()

    items = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [i for i in items if i["id"] in wanted]
    if args.limit:
        items = items[: args.limit]

    results = []
    for n, item in enumerate(items, 1):
        try:
            rec = evaluate_one(item)
        except Exception as exc:  # keep going; an eval crash counts as a failure
            rec = {"id": item["id"], "type": item["type"], "question": item["question"], "latency_ms": 0,
                   "error": f"eval crashed: {exc}", "needs_clarification": False, "sql": None, "grounded": False,
                   "answer": None, "sql_attempts": None, "expected_clarification": bool(item.get("clarify")),
                   "valid_sql": False, "execution_match": False, "false_clarification": False, "pass": False}
        results.append(rec)
        print(f"[{n}/{len(items)}] {'PASS' if rec['pass'] else 'FAIL'}  {rec['id']}  ({rec['latency_ms'] / 1000:.1f}s)", flush=True)

    s = get_settings()
    out = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "models": {"strong": s.llm_model_strong, "fast": s.llm_model_fast, "fallback": s.llm_model_fallback},
        "summary": summarize(results), "results": results,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["summary"], indent=2))
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
