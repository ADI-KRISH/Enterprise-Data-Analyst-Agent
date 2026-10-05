"""Static SQL validation (sqlglot, dialect postgres) before anything touches the database.

Rules (CLAUDE.md rule 3): one statement, top-level SELECT / WITH…SELECT, no DML/DDL/DCL, no pg_* functions,
no comments containing statements, every table and column in the allow-list, LIMIT enforced.
The EXPLAIN dry-run lives in agents/executor.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

from app.config import PROJECT_ROOT, get_settings

DIALECT = "postgres"
ALLOWLIST_PATH = PROJECT_ROOT / "database" / "allowlist.yaml"

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Drop", "Alter", "AlterTable", "Create", "TruncateTable",
    "Grant", "Revoke", "Copy", "Command", "Into", "Lock", "Set", "SetItem", "Transaction", "Commit",
    "Rollback", "Use", "Pragma", "Analyze", "Execute", "Declare", "Show", "Describe", "LoadData", "Attach",
)
_FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))
_FORBIDDEN_FUNC_RE = re.compile(
    r"^(pg_|lo_|dblink|set_config|current_setting|query_to_xml|xpath|txid_|nextval|setval|currval|"
    r"generate_series_big|version|inet_|current_user|session_user|current_database|current_schema)", re.I)
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STATEMENT_WORD_RE = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|select|execute|set)\b|;", re.I)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    sql: str = ""  # normalized SQL (comments stripped, LIMIT enforced); only meaningful when ok

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors}


@lru_cache
def load_allowlist(path: str = str(ALLOWLIST_PATH)) -> dict[str, list[str]]:
    data = yaml.safe_load(open(path, encoding="utf-8"))
    return {t: list(cols) for t, cols in data["tables"].items()}


def _schema_name() -> str:
    return yaml.safe_load(open(ALLOWLIST_PATH, encoding="utf-8")).get("schema", "analytics")


def validate_sql(
    sql: str,
    allowlist: dict[str, list[str]] | None = None,
    default_limit: int | None = None,
) -> ValidationResult:
    allowlist = allowlist if allowlist is not None else load_allowlist()
    limit = default_limit or get_settings().default_row_limit
    schema = _schema_name()
    errors: list[str] = []

    if not sql or not sql.strip():
        return ValidationResult(False, ["SQL is empty"])

    for comment in _COMMENT_RE.findall(sql):
        if _STATEMENT_WORD_RE.search(comment):
            return ValidationResult(False, ["SQL comments must not contain statements or keywords; remove all comments"])

    try:
        statements = [s for s in sqlglot.parse(sql, dialect=DIALECT) if s is not None]
    except SqlglotError as exc:
        return ValidationResult(False, [f"SQL does not parse as PostgreSQL: {str(exc)[:300]}"])
    if len(statements) != 1:
        return ValidationResult(False, [f"Exactly one statement is allowed, found {len(statements)} (no ';' chaining)"])
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return ValidationResult(False, [f"Only SELECT queries are allowed, got {type(tree).__name__.upper()}"])

    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, _FORBIDDEN_NODES):
            errors.append(f"Forbidden construct: {type(node).__name__.upper()}")
        if isinstance(node, exp.Func):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if _FORBIDDEN_FUNC_RE.match(name or ""):
                errors.append(f"Forbidden function: {name}")
    if errors:
        return ValidationResult(False, sorted(set(errors)))

    # ---- tables (CTE names are allowed)
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names and not table.db:
            continue
        if table.db and table.db.lower() != schema:
            errors.append(f"Schema '{table.db}' is not allowed; only '{schema}' tables may be queried")
        elif name not in allowlist:
            errors.append(f"Unknown table '{table.name}'. Allowed tables: {', '.join(sorted(allowlist))}")
    if errors:
        return ValidationResult(False, sorted(set(errors)))

    # ---- columns (qualify resolves aliases, CTEs, subqueries against the schema)
    mapping = {schema: {t: {c: "text" for c in cols} for t, cols in allowlist.items()}}
    try:
        qualify(tree.copy(), db=schema, schema=mapping, dialect=DIALECT,
                validate_qualify_columns=True, identify=False)
    except Exception as exc:  # sqlglot raises OptimizeError / ParseError with the column name
        return ValidationResult(False, [f"Column/reference error: {str(exc)[:300]}"])

    # ---- LIMIT enforcement
    existing = tree.args.get("limit")
    if existing is None:
        tree = tree.limit(limit, copy=False)
    else:
        expr = existing.args.get("expression")
        if isinstance(expr, exp.Literal) and expr.is_int and int(expr.name) > limit:
            tree = tree.limit(limit, copy=False)

    return ValidationResult(True, [], tree.sql(dialect=DIALECT, comments=False))
