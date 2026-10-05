import re

from agents.sql_validator import load_allowlist
from app.config import PROJECT_ROOT


def _schema_columns() -> dict[str, list[str]]:
    cols: dict[str, list[str]] = {}
    current = None
    for line in (PROJECT_ROOT / "database" / "schema.sql").read_text().splitlines():
        if m := re.match(r"CREATE TABLE (\w+)", line):
            current = m.group(1)
            cols[current] = []
        elif line.startswith(");"):
            current = None
        elif current and (m := re.match(r"\s+(\w+)\s+[A-Z]", line)):
            cols[current].append(m.group(1))
    return cols


def test_allowlist_matches_schema_sql():
    assert load_allowlist() == _schema_columns()
