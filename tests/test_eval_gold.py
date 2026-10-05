"""Gold SQL in eval/questions.yaml must validate and return rows; matcher sanity."""

import pandas as pd
import pytest
import yaml
from sqlalchemy import text

from agents.executor import execute_sql, get_engine
from agents.sql_validator import validate_sql
from eval.run_eval import QUESTIONS, results_match

QS = yaml.safe_load(QUESTIONS.read_text())["questions"]


def test_matcher():
    gold = pd.DataFrame({"c": ["A", "B"], "v": [1.0, 2.0]})
    assert results_match(gold, pd.DataFrame({"x": ["B", "A", "C"], "y": [2.004, 1.0, 3]}))
    assert not results_match(gold, pd.DataFrame({"x": ["A", "B"], "y": [1.0, 5.0]}))


@pytest.mark.parametrize("q", [q for q in QS if q.get("gold_sql")], ids=lambda q: q["id"])
def test_gold_sql(q):
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("database not reachable")
    v = validate_sql(q["gold_sql"])
    assert v.ok, v.errors
    r = execute_sql(v.sql)
    assert r.ok and len(r.df) > 0, r.error


def test_ids_unique():
    assert len({q["id"] for q in QS}) == len(QS)
