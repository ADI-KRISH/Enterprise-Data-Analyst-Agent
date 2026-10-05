"""Run the evaluation set through the real agent and save results to eval/results/<ts>.json.

    python -m eval.run_eval [--limit N] [--ids q01,q08]

Requires a configured LLM (OPENROUTER_API_KEY) and the seeded database. Metrics are computed
here from actual runs; nothing is estimated. Result match = every numeric column and every
label column of the gold result appears (as a column, within tolerance) in the agent's result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import pandas as pd
import yaml

from agents.executor import execute_sql, frame_to_df
from app.config import PROJECT_ROOT, get_settings
from app.graph import run_agent

QUESTIONS = Path(__file__).parent / "questions.yaml"
RESULTS = Path(__file__).parent / "results"


def _col_values(s: pd.Series, numeric: bool):
    if numeric:
        return sorted(round(float(x), 2) for x in s.dropna())
    return sorted(str(x) for x in s.dropna())


def results_match(gold: pd.DataFrame, got: pd.DataFrame, tol: float = 0.02) -> bool:
    """Order-insensitive, alias-insensitive comparison of gold vs agent output."""
    if gold.empty or got.empty:
        return gold.empty and got.empty
    for gc in gold.columns:
        g_num = pd.api.types.is_numeric_dtype(gold[gc])
        g_vals = _col_values(gold[gc], g_num)
        ok = False
        for ac in got.columns:
            a_num = pd.api.types.is_numeric_dtype(got[ac])
            if g_num != a_num:
                continue
            a_vals = _col_values(got[ac], a_num)
            if g_num:
                ok = len(a_vals) >= len(g_vals) and all(
                    any(abs(g - a) <= tol + 1e-6 * abs(g) for a in a_vals) for g in g_vals)
            else:
                ok = set(g_vals) <= set(a_vals)
            if ok:
                break
        if not ok:
            return False
    return True


def evaluate(selected: list[dict]) -> dict:
    rows = []
    for q in selected:
        out = run_agent(q["question"])
        validation = [e for e in out.get("trace", []) if e.get("node") == "validate"]
        row = {
            "id": q["id"], "category": q["category"], "question": q["question"],
            "expect_clarification": bool(q.get("expect_clarification")),
            "asked_clarification": bool(out.get("needs_clarification")),
            "error": out.get("error"), "sql": out.get("sql"),
            "sql_attempts": out.get("sql_attempts", 0),
            "first_try_valid": bool(validation and validation[0].get("ok")),
            "executed": bool(out.get("result_df")), "grounded": out.get("grounded"),
            "latency_ms": out["latency_ms"], "answer": out.get("answer"),
        }
        if q.get("expect_clarification"):
            row["correct"] = row["asked_clarification"]
        else:
            gold = execute_sql(q["gold_sql"]).df
            got = frame_to_df(out.get("result_df")) if out.get("result_df") else pd.DataFrame()
            row["result_match"] = bool(gold is not None and results_match(gold, got))
            row["correct"] = row["result_match"] and not row["asked_clarification"]
        rows.append(row)
        print(f"{q['id']}: correct={row['correct']} grounded={row['grounded']} {row['latency_ms']}ms")

    answerable = [r for r in rows if not r["expect_clarification"]]
    clar = [r for r in rows if r["expect_clarification"]]
    pct = lambda n, d: round(100 * n / d, 1) if d else None  # noqa: E731
    lat = [r["latency_ms"] for r in rows]
    s = get_settings()
    return {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "models": {"strong": s.llm_model_strong, "fast": s.llm_model_fast, "fallback": s.llm_model_fallback},
        "summary": {
            "questions": len(rows),
            "answerable": len(answerable),
            "result_match_pct": pct(sum(r["correct"] for r in answerable), len(answerable)),
            "executed_pct": pct(sum(r["executed"] for r in answerable), len(answerable)),
            "first_try_valid_sql_pct": pct(sum(r["first_try_valid"] for r in answerable), len(answerable)),
            "grounded_pct": pct(sum(bool(r["grounded"]) for r in answerable), len(answerable)),
            "clarification_correct_pct": pct(sum(r["correct"] for r in clar), len(clar)),
            "errors": sum(bool(r["error"]) for r in rows),
            "latency_ms_median": int(statistics.median(lat)) if lat else None,
            "latency_ms_max": max(lat) if lat else None,
        },
        "results": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma-separated question ids")
    args = ap.parse_args()
    if get_settings().openrouter_api_key is None:
        print("OPENROUTER_API_KEY is not set; refusing to run (no results are fabricated).")
        return 2
    qs = yaml.safe_load(QUESTIONS.read_text())["questions"]
    if args.ids:
        qs = [q for q in qs if q["id"] in set(args.ids.split(","))]
    if args.limit:
        qs = qs[: args.limit]
    report = evaluate(qs)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["summary"], indent=2))
    print(f"saved {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
