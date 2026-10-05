"""SQL validation (sqlglot, dialect postgres). Every statement passes here before execution.

Rules (CLAUDE.md #3): single SELECT / WITH..SELECT statement, no DML/DDL/admin,
no pg_* functions, no statement-bearing comments, only allow-listed tables and
columns, LIMIT enforced. The returned `sql` is regenerated from the parsed tree
(comments dropped), and that is what gets executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from app.config import PROJECT_ROOT, get_settings

DIALECT = "postgres"
ALLOWLIST_PATH = PROJECT_ROOT / "database" / "allowlist.yaml"

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Drop", "Alter", "Create", "TruncateTable",
        "Grant", "Revoke", "Copy", "Command", "Into", "Set", "Use", "Transaction", "Commit",
        "Rollback", "Analyze", "Describe", "Show", "Kill", "Pragma", "Execute", "LoadData",
        "Lock",
    )
    if hasattr(exp, name)
)
_FORBIDDEN_FUNCS = re.compile(
    r"^(pg_.*|set_config|current_setting|lo_.*|dblink.*|query_to_xml.*|xmlparse|"
    r"txid_.*|nextval|setval|currval)$",
    re.IGNORECASE,
)
_COMMENT_STATEMENT = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|"
    r"select|set|execute)\b|;",
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    ok: bool
    sql: str | None = None  # normalised SQL to execute (LIMIT enforced), only when ok
    errors: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    limit_added: bool = False

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "tables": self.tables,
                "limit_added": self.limit_added}


@lru_cache
def load_allowlist(path: str = str(ALLOWLIST_PATH)) -> dict:
    doc = yaml.safe_load(Path(path).read_text())
    return {
        "schema": doc["schema"],
        "tables": {t: set(spec["columns"]) for t, spec in doc["tables"].items()},
    }


def _sqlglot_schema(allow: dict) -> dict:
    return {allow["schema"]: {t: {c: "text" for c in cols} for t, cols in allow["tables"].items()}}


def validate_sql(
    sql: str, *, allowlist: dict | None = None, default_limit: int | None = None
) -> ValidationResult:
    allow = allowlist or load_allowlist()
    limit = default_limit or get_settings().default_row_limit
    if not sql or not sql.strip():
        return ValidationResult(False, errors=["Empty SQL."])

    try:
        statements = [s for s in sqlglot.parse(sql, dialect=DIALECT) if s is not None]
    except (ParseError, SqlglotError) as exc:
        return ValidationResult(False, errors=[f"SQL does not parse: {str(exc)[:300]}"])
    if len(statements) != 1:
        return ValidationResult(
            False, errors=[f"Exactly one statement is allowed, got {len(statements)}."]
        )
    tree = statements[0]

    errors: list[str] = []
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return ValidationResult(
            False, errors=[f"Only SELECT queries are allowed (got {type(tree).__name__})."]
        )

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            errors.append(f"Forbidden operation: {type(node).__name__}.")
        for comment in node.comments or []:
            if _COMMENT_STATEMENT.search(comment):
                errors.append("Comments must not contain SQL statements or ';'.")
        fname = None
        if isinstance(node, exp.Anonymous):
            fname = node.name
        elif isinstance(node, exp.Func):
            fname = node.sql_name()
        if fname and _FORBIDDEN_FUNCS.match(fname):
            errors.append(f"Forbidden function: {fname}.")
    if errors:
        return ValidationResult(False, errors=sorted(set(errors)))

    # --- allow-list: tables
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    used_tables: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not table.db and name in cte_names:
            continue
        if table.db and table.db.lower() != allow["schema"]:
            errors.append(f"Schema '{table.db}' is not allowed; use '{allow['schema']}'.")
        elif name not in allow["tables"]:
            errors.append(f"Unknown table '{table.name}'. Allowed: {', '.join(sorted(allow['tables']))}.")
        else:
            used_tables.append(name)
            if not table.db:
                table.set("db", exp.to_identifier(allow["schema"]))
    if errors:
        return ValidationResult(False, errors=sorted(set(errors)))

    # --- allow-list: columns (scope-aware, via sqlglot qualify)
    try:
        qualified = qualify(
            tree.copy(), schema=_sqlglot_schema(allow), db=allow["schema"], dialect=DIALECT,
            validate_qualify_columns=True, expand_stars=True, identify=False,
        )
        for col in qualified.find_all(exp.Column):
            tbl = col.table.lower()
            if tbl in allow["tables"] and col.name.lower() not in allow["tables"][tbl]:
                errors.append(f"Unknown column '{tbl}.{col.name}'.")
    except OptimizeError as exc:
        errors.append(f"Column check failed: {str(exc)[:300]}")
    except SqlglotError as exc:
        errors.append(f"SQL could not be analysed: {str(exc)[:300]}")
    if errors:
        return ValidationResult(False, errors=sorted(set(errors)))

    # --- LIMIT
    limit_added = False
    if tree.args.get("limit") is None and not _has_fetch(tree):
        tree.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
        limit_added = True

    return ValidationResult(
        True,
        sql=tree.sql(dialect=DIALECT, comments=False),
        tables=sorted(set(used_tables)),
        limit_added=limit_added,
    )


def _has_fetch(tree: exp.Expression) -> bool:
    return tree.args.get("fetch") is not None
