"""Validate LLM-generated SQL before it reaches the database (CLAUDE.md rule 3, Security §1).

`validate(sql)` parses with sqlglot (postgres) and accepts only a single read-only query
over allow-listed tables/columns using allow-listed functions. It returns the SQL to
execute, regenerated from the checked syntax tree (comments dropped, columns qualified,
row limit applied), so the database runs exactly what was validated rather than the raw text.

Everything here is an allowlist: unknown statement types, functions, casts, schemas,
tables and columns are rejected. Error messages describe the problem in our own words
and are fed back to the SQL generator; they are never shown to users.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope

from database.allowlist import Allowlist, load_allowlist

DIALECT = "postgres"
MAX_SQL_CHARS = 20_000

# --- functions -----------------------------------------------------------------------
# sqlglot parses most known functions into typed nodes; these are the allowed ones
# (CLAUDE.md Security §1). Clock functions are deliberately absent (rule 6).
ALLOWED_FUNCTION_NODES: frozenset[type[exp.Expression]] = frozenset({
    # aggregates
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Stddev, exp.StddevSamp,
    exp.StddevPop, exp.PercentileCont,
    # date
    exp.TimestampTrunc, exp.DateTrunc, exp.Extract, exp.TimeToStr, exp.ToChar,
    # math
    exp.Round, exp.Abs, exp.Ceil, exp.Floor, exp.Greatest, exp.Least, exp.Nullif,
    # string
    exp.Lower, exp.Upper, exp.Trim, exp.Length, exp.Concat, exp.Substring,
    # conditional / conversion
    exp.Case, exp.If, exp.Coalesce, exp.Cast,
    # window
    exp.RowNumber, exp.Rank, exp.DenseRank, exp.Lag, exp.Lead, exp.Ntile,
    exp.FirstValue, exp.LastValue,
})
# Functions sqlglot leaves as Anonymous in the postgres dialect (unqualified name only).
ALLOWED_ANONYMOUS_FUNCTIONS: frozenset[str] = frozenset({"make_date", "date_part"})
# Operator nodes that sqlglot models as functions.
ALLOWED_OPERATOR_FUNCS: frozenset[type[exp.Expression]] = frozenset({exp.And, exp.Or, exp.Exists})

# --- casts -----------------------------------------------------------------------------
ALLOWED_CAST_TYPES: frozenset[exp.DataType.Type] = frozenset({
    exp.DataType.Type.SMALLINT, exp.DataType.Type.INT, exp.DataType.Type.BIGINT,
    exp.DataType.Type.DECIMAL, exp.DataType.Type.FLOAT, exp.DataType.Type.DOUBLE,
    exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR, exp.DataType.Type.CHAR,
    exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ,
    exp.DataType.Type.INTERVAL, exp.DataType.Type.BOOLEAN,
})

# --- statements / constructs -----------------------------------------------------------
TOP_LEVEL_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.DML,  # Insert, Update, Delete, Merge, Copy
    exp.DDL,  # Create, Drop, ...
    exp.Alter, exp.TruncateTable, exp.Grant, exp.Command, exp.Set, exp.Transaction,
    exp.Commit, exp.Rollback, exp.Into, exp.Lock, exp.Placeholder, exp.Parameter,
    exp.Pragma, exp.Use, exp.Describe,
    exp.TableColumn,  # whole-row reference: SELECT c FROM dim_customer c
)
FORBIDDEN_NODE_NAMES = frozenset({"Revoke", "Show", "Analyze", "Kill", "Refresh", "Cache", "Uncache"})
# Statement keywords that must not appear inside comments (comment smuggling).
_COMMENT_STATEMENT = re.compile(
    r";|\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|"
    r"set|show|listen|notify|unlisten|execute|prepare|vacuum|lock)\b",
    re.IGNORECASE,
)

GENERIC_PARSE_ERROR = "could not parse the SQL as a single PostgreSQL query"


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    normalized_sql: str | None = None
    limit: int | None = None  # LIMIT in normalized_sql
    limit_imposed: bool = False  # True if we injected or clamped it (results may be cut off)


class _Rejected(Exception):
    pass


def _fail(errors: list[str]) -> ValidationResult:
    # de-duplicate while keeping order
    return ValidationResult(ok=False, errors=list(dict.fromkeys(errors)), normalized_sql=None)


# ------------------------------------------------------------------ structural checks

def _check_statement(tree: exp.Expression, errors: list[str]) -> None:
    if not isinstance(tree, TOP_LEVEL_TYPES):
        errors.append(f"only SELECT queries are allowed (got {type(tree).__name__.upper()})")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES) or type(node).__name__ in FORBIDDEN_NODE_NAMES:
            errors.append(f"forbidden construct: {type(node).__name__.upper()}")
        elif isinstance(node, exp.Query) and not isinstance(node, (*TOP_LEVEL_TYPES, exp.Subquery)):
            errors.append(f"forbidden construct: {type(node).__name__.upper()}")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            errors.append("recursive CTEs are not allowed")
        if isinstance(node, exp.Select) and (node.args.get("locks") or node.args.get("into")):
            errors.append("locking clauses and SELECT INTO are not allowed")
        for comment in node.comments or []:
            if _COMMENT_STATEMENT.search(comment):
                errors.append("comments must not contain statements or ';'")


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def _check_functions(tree: exp.Expression, errors: list[str]) -> None:
    for node in tree.walk():
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            errors.append(f"schema-qualified function calls are not allowed: {_function_name(node.expression)}")
            continue
        if isinstance(node, exp.Func):
            if type(node) in ALLOWED_FUNCTION_NODES or type(node) in ALLOWED_OPERATOR_FUNCS:
                pass
            elif isinstance(node, exp.Anonymous) and _function_name(node) in ALLOWED_ANONYMOUS_FUNCTIONS \
                    and not isinstance(node.parent, exp.Dot):
                pass
            else:
                errors.append(f"function not allowed: {_function_name(node)}")
        elif isinstance(node, (exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentTime, exp.CurrentUser)):
            errors.append(f"function not allowed: {type(node).__name__.lower()}")
        if isinstance(node, exp.Cast):
            to = node.args.get("to")
            if not isinstance(to, exp.DataType) or to.this not in ALLOWED_CAST_TYPES:
                errors.append(f"cast to type not allowed: {to.sql(DIALECT) if to else '?'}")


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name for cte in tree.find_all(exp.CTE)}


def _check_tables(tree: exp.Expression, allowlist: Allowlist, errors: list[str]) -> None:
    ctes = _cte_names(tree)
    for table in tree.find_all(exp.Table):
        name, db, catalog = table.name, table.db, table.catalog
        shown = ".".join(p for p in (catalog, db, name) if p)
        if not isinstance(table.this, exp.Identifier):
            errors.append(f"table functions are not allowed: {shown or table.sql(DIALECT)}")
        elif catalog:
            errors.append(f"cross-database references are not allowed: {shown}")
        elif db and db != allowlist.schema:
            errors.append(f"schema not allowed: {db} (only {allowlist.schema})")
        elif not db and name in ctes:
            continue
        elif name not in allowlist.tables:
            errors.append(f"unknown table: {shown}")


# ------------------------------------------------------------- columns and PII output

def _schema_for_qualify(allowlist: Allowlist) -> dict:
    return {allowlist.schema: {t: {c: "text" for c in spec.columns} for t, spec in allowlist.tables.items()}}


class _Lineage:
    """Tracks which query outputs depend on restricted base columns."""

    def __init__(self, root: Scope, allowlist: Allowlist) -> None:
        self.allowlist = allowlist
        self.by_expr = {id(s.expression): s for s in root.traverse()}
        self._memo: dict[int, list[bool]] = {}

    def outputs(self, scope: Scope) -> list[bool]:
        """Per output column of `scope`: does it expose a restricted column?"""
        key = id(scope)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = []  # cycle guard (recursive CTEs are rejected earlier anyway)
        if scope.set_operation_scopes:
            parts = [self.outputs(s) for s in scope.set_operation_scopes]
            width = max(len(p) for p in parts)
            result = [any(p[i] for p in parts if i < len(p)) for i in range(width)]
        elif isinstance(scope.expression, exp.Select):
            result = [self.expr_restricted(p, scope) for p in scope.expression.expressions]
        else:
            # LATERAL / VALUES / other table-producing scopes: no named projections to
            # track, so treat every output as restricted if anything inside is.
            result = [self.expr_restricted(scope.expression, scope)]
        self._memo[key] = result
        return result

    def output_by_name(self, scope: Scope, name: str) -> bool:
        outs = self.outputs(scope)
        expr = scope.expression
        while isinstance(expr, exp.SetOperation):  # names come from the left-most branch
            expr = expr.this
        names = [p.alias_or_name for p in expr.expressions] if isinstance(expr, exp.Select) else []
        return any(o for n, o in zip(names, outs) if n == name) if name in names else any(outs)

    def _resolve(self, column: exp.Column, scope: Scope | None):
        while scope is not None:
            if column.table in scope.sources:
                return scope.sources[column.table]
            scope = scope.parent
        return None

    def expr_restricted(self, node: exp.Expression, scope: Scope) -> bool:
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, exp.Count):
                continue  # COUNT(...) over a restricted column is allowed
            if isinstance(n, (exp.Select, exp.SetOperation)):
                sub = self.by_expr.get(id(n))
                if sub is None or any(self.outputs(sub)):
                    return True
                continue
            if isinstance(n, exp.Column):
                source = self._resolve(n, scope)
                if isinstance(source, exp.Table):
                    spec = self.allowlist.tables.get(source.name)
                    if spec is None or n.name in spec.restricted:
                        return True
                elif isinstance(source, Scope):
                    if self.output_by_name(source, n.name):
                        return True
                else:
                    return True  # unresolved: be conservative
                continue
            stack.extend(n.iter_expressions())
        return False


def _scope_of(node: exp.Expression, by_expr: dict[int, Scope]) -> Scope | None:
    parent = node.parent
    while parent is not None:
        if id(parent) in by_expr:
            return by_expr[id(parent)]
        parent = parent.parent
    return None


def _projection_aliases(scope: Scope) -> set[str]:
    expr = scope.expression
    while isinstance(expr, exp.SetOperation):
        expr = expr.this
    return {p.alias_or_name for p in expr.expressions} if isinstance(expr, exp.Select) else set()


def _check_columns(tree: exp.Expression, allowlist: Allowlist, errors: list[str]) -> None:
    root = build_scope(tree)
    if root is None:
        errors.append(GENERIC_PARSE_ERROR)
        return
    by_expr = {id(s.expression): s for s in root.traverse()}
    # Every column reference in the tree (not only those sqlglot's scope tracks).
    for column in tree.find_all(exp.Column):
        scope = _scope_of(column, by_expr)
        if scope is None:
            errors.append(f"unresolved column: {column.sql(DIALECT)}")
            continue
        if not column.table:
            # After qualification only references to output aliases (ORDER BY x) stay
            # unqualified. A bare name equal to a table alias is a whole-row reference.
            visible = set()
            s: Scope | None = scope
            while s is not None:
                visible |= set(s.sources)
                s = s.parent
            if column.name in visible or column.name not in _projection_aliases(scope):
                errors.append(f"unresolved column or whole-row reference: {column.name}")
            continue
        source, s = None, scope
        while s is not None and source is None:
            source = s.sources.get(column.table)
            s = s.parent
        if isinstance(source, exp.Table):
            spec = allowlist.tables.get(source.name)
            if spec is None or column.name not in spec.columns:
                errors.append(f"unknown column: {source.name}.{column.name}")
        elif source is None:
            errors.append(f"unknown column: {column.sql(DIALECT)}")
    if errors:
        return
    lineage = _Lineage(root, allowlist)
    if any(lineage.outputs(root)):
        restricted = sorted(f"{t}.{c}" for t, s in allowlist.tables.items() for c in s.restricted)
        errors.append(
            f"restricted column(s) {', '.join(restricted)} may be used in WHERE/JOIN/GROUP BY/"
            "HAVING/ORDER BY or inside COUNT(), but must not be returned in the output"
        )


# ------------------------------------------------------------------------------- limit

def _apply_limit(tree: exp.Expression, default_limit: int, max_rows: int) -> tuple[int, bool]:
    """Set a literal LIMIT: default if missing/non-literal, clamped to max_rows.
    FETCH FIRST n ROWS (stored by sqlglot under the same arg) becomes LIMIT n."""
    limit = tree.args.get("limit")
    value = None
    if isinstance(limit, exp.Fetch):
        value = limit.args.get("count")
    elif isinstance(limit, exp.Limit):
        value = limit.expression
    requested = int(value.name) if isinstance(value, exp.Literal) and value.is_int else None
    n = min(requested, max_rows) if requested and requested > 0 else default_limit
    tree.set("limit", exp.Limit(expression=exp.Literal.number(n)))
    return n, n != requested


# -------------------------------------------------------------------------------- main

def validate(
    sql: str,
    *,
    allowlist: Allowlist | None = None,
    default_limit: int | None = None,
    max_rows: int | None = None,
) -> ValidationResult:
    """Validate one LLM-generated query; return the normalized SQL to execute if ok.

    Never raises: any unexpected failure (e.g. RecursionError on pathological nesting)
    is a rejection.
    """
    try:
        return _validate(sql, allowlist=allowlist, default_limit=default_limit, max_rows=max_rows)
    except RecursionError:
        return _fail(["query is nested too deeply"])
    except Exception:  # noqa: BLE001 - fail closed
        return _fail([GENERIC_PARSE_ERROR])


def _validate(
    sql: str,
    *,
    allowlist: Allowlist | None,
    default_limit: int | None,
    max_rows: int | None,
) -> ValidationResult:
    if default_limit is None or max_rows is None:
        from app.config import get_settings

        s = get_settings()
        default_limit = default_limit or s.default_row_limit
        max_rows = max_rows or s.max_result_rows
    default_limit = min(default_limit, max_rows)
    allowlist = allowlist or load_allowlist()

    if not isinstance(sql, str) or not sql.strip():
        return _fail(["empty SQL"])
    if len(sql) > MAX_SQL_CHARS:
        return _fail([f"SQL longer than {MAX_SQL_CHARS} characters"])
    if "\x00" in sql:
        return _fail(["SQL contains a NUL byte"])

    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError:
        return _fail([GENERIC_PARSE_ERROR])
    if len(statements) != 1:
        return _fail([f"exactly one statement is allowed (got {len(statements)})"])
    tree = statements[0]

    errors: list[str] = []
    _check_statement(tree, errors)
    if errors:
        return _fail(errors)

    tree = normalize_identifiers(tree, dialect=DIALECT)
    _check_functions(tree, errors)
    _check_tables(tree, allowlist, errors)
    if errors:
        return _fail(errors)

    try:
        tree = qualify(
            tree,
            dialect=DIALECT,
            db=allowlist.schema,
            schema=_schema_for_qualify(allowlist),
            expand_stars=True,
            validate_qualify_columns=True,
            quote_identifiers=False,
            infer_schema=False,
        )
    except OptimizeError as e:
        msg = str(e).splitlines()[0][:150]
        return _fail([f"column/table reference could not be resolved: {msg}"])
    except SqlglotError:
        return _fail([GENERIC_PARSE_ERROR])

    # qualify may rewrite; re-run the structural checks on what will actually execute.
    _check_statement(tree, errors)
    _check_functions(tree, errors)
    _check_tables(tree, allowlist, errors)
    _check_columns(tree, allowlist, errors)
    if errors:
        return _fail(errors)

    limit, imposed = _apply_limit(tree, default_limit, max_rows)
    normalized = tree.sql(dialect=DIALECT, comments=False, pretty=False)

    # Round-trip guard: the text we will execute must parse back to exactly one query.
    try:
        reparsed = [s for s in sqlglot.parse(normalized, read=DIALECT) if s is not None]
    except SqlglotError:
        return _fail([GENERIC_PARSE_ERROR])
    if len(reparsed) != 1 or not isinstance(reparsed[0], TOP_LEVEL_TYPES):
        return _fail([GENERIC_PARSE_ERROR])
    return ValidationResult(
        ok=True, errors=[], normalized_sql=normalized, limit=limit, limit_imposed=imposed
    )
