import pandas as pd
import yaml

from agents.executor import run_query
from agents.sql_validator import validate_sql
from app.config import PROJECT_ROOT
from eval.run_eval import results_match, summarize

ITEMS = yaml.safe_load((PROJECT_ROOT / "eval" / "questions.yaml").read_text())["questions"]
GOLD = [i for i in ITEMS if not i.get("clarify")]


def test_question_file_shape():
    assert len({i["id"] for i in ITEMS}) == len(ITEMS)
    assert all(i.get("gold_sql") for i in GOLD)
    assert all("gold_sql" not in i for i in ITEMS if i.get("clarify"))
    assert any(i.get("clarify") for i in ITEMS)


def test_gold_sql_passes_validator_and_runs_read_only(db):
    for item in GOLD:
        v = validate_sql(item["gold_sql"])
        assert v.ok, (item["id"], v.errors)
        df = run_query(v.sql)
        assert len(df) > 0, item["id"]


def test_results_match_semantics():
    gold = pd.DataFrame({"region": ["N", "S"], "revenue": [10.0, 20.0]})
    assert results_match(gold, pd.DataFrame({"r": ["s", "n"], "total": [20.004, 10.0], "extra": [1, 2]}))
    assert not results_match(gold, pd.DataFrame({"r": ["N", "S"], "total": [10.0, 25.0]}))
    assert not results_match(gold, pd.DataFrame({"r": ["N"], "total": [10.0]}))


def test_summarize():
    rows = [
        {"expected_clarification": False, "execution_match": True, "valid_sql": True, "grounded": True,
         "false_clarification": False, "error": None, "latency_ms": 100},
        {"expected_clarification": True, "needs_clarification": True, "error": None, "latency_ms": 300},
    ]
    s = summarize(rows)
    assert s["execution_accuracy"] == 1.0 and s["clarification_recall"] == 1.0 and s["latency_ms"]["mean"] == 200
