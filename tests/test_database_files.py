"""Offline checks for database/ artefacts (no DB connection needed)."""

from datetime import date
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

from database.seed import date_row, fiscal_quarter, fiscal_year_label, to_conninfo

DB_DIR = Path(__file__).resolve().parent.parent / "database"


def schema_columns() -> dict[str, list[str]]:
    tables = {}
    for stmt in sqlglot.parse((DB_DIR / "schema.sql").read_text(), dialect="postgres"):
        if isinstance(stmt, exp.Create) and stmt.kind == "TABLE":
            table = stmt.this.this
            assert table.db == "analytics"
            tables[table.name] = [c.name for c in stmt.this.expressions if isinstance(c, exp.ColumnDef)]
    return tables


def test_allowlist_matches_schema():
    allow = yaml.safe_load((DB_DIR / "allowlist.yaml").read_text())
    assert allow["schema"] == "analytics"
    assert {t: v["columns"] for t, v in allow["tables"].items()} == schema_columns()


def test_fiscal_calendar():
    assert fiscal_year_label(date(2026, 4, 1)) == "FY2026-27"
    assert fiscal_year_label(date(2026, 3, 31)) == "FY2025-26"
    assert fiscal_year_label(date(1999, 12, 1)) == "FY1999-00"
    assert [fiscal_quarter(date(2026, m, 1)) for m in range(1, 13)] == [4, 4, 4, 1, 1, 1, 2, 2, 2, 3, 3, 3]


def test_date_row():
    assert date_row(date(2026, 9, 30)) == (
        20260930, date(2026, 9, 30), 30, 9, "September", 3, 2026, 2, "FY2026-27")


def test_to_conninfo():
    assert to_conninfo("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
