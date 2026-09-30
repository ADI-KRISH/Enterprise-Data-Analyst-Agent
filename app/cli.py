"""Command-line entry point: python -m app.cli "question" [--json]

Prints the SQL, the result table, the answer and a trace summary.
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from app.graph import run_agent

MAX_QUESTION_CHARS = 500
MAX_TABLE_ROWS = 20


def _section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def _trace_summary(response: dict) -> None:
    node_events = [e for e in response["trace"] if e.get("event") == "node"]
    llm_calls = [e for e in response["trace"] if e.get("event") == "llm_call"]
    for e in node_events:
        model = e.get("model") or "-"
        detail = json.dumps(e["detail"], default=str) if e.get("detail") else ""
        print(f"  {e['node']:<16} {e['status']:<22} {e['latency_ms']:>9.1f} ms  {model:<42} {detail}")
    print(f"  llm calls: {len(llm_calls)}   total latency: {response['latency_ms'] / 1000:.1f} s")


def print_response(response: dict) -> None:
    _section("SQL")
    if response.get("queries"):
        for i, q in enumerate(response["queries"], 1):
            if len(response["queries"]) > 1:
                print(f"-- query {i}: {q['purpose']} ({q['row_count']} rows)")
            print(q["sql"] + ";\n")
    else:
        print("(none)")

    _section("Result")
    if response.get("columns"):
        df = pd.DataFrame(response["data"], columns=response["columns"])
        with pd.option_context("display.width", 160, "display.max_columns", 20,
                               "display.float_format", "{:,.2f}".format):
            print(df.head(MAX_TABLE_ROWS).to_string(index=False))
        if len(df) > MAX_TABLE_ROWS:
            print(f"... {len(df) - MAX_TABLE_ROWS} more rows")
    else:
        print("(no data)")

    _section("Answer")
    print(response.get("answer") or "(no answer)")
    flags = {k: response.get(k) for k in ("grounded", "refused", "refusal_reason", "needs_clarification",
                                          "output_blocked", "error")}
    if response.get("time_range"):
        flags["time_range"] = response["time_range"].get("label")
    print("\n" + "  ".join(f"{k}={v}" for k, v in flags.items() if v not in (None, False)))
    print(f"request_id={response['request_id']}")

    _section("Trace")
    _trace_summary(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Ask the data analyst agent.")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="print the full JSON response")
    args = parser.parse_args(argv)
    if len(args.question) > MAX_QUESTION_CHARS:
        print(f"question longer than {MAX_QUESTION_CHARS} characters", file=sys.stderr)
        return 2
    response = run_agent(args.question)
    if args.json:
        print(json.dumps(response, indent=2, default=str, ensure_ascii=False))
    else:
        print(f"Q: {args.question}")
        print_response(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
