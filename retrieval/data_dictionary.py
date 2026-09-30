"""Generate the data dictionary from the live database so it stays in sync with the schema.

Usage:  python -m retrieval.data_dictionary      (connects as analyst_ro, DATABASE_URL_RO)

Writes retrieval/docs/data_dictionary.json (structured; used for schema cards) and
retrieval/docs/data_dictionary.md (rendered from the JSON; indexed for retrieval).
Types, keys and row counts come from pg_catalog; example values are queried from the
tables; column meanings come from retrieval/column_notes.yaml. Only allow-listed tables
and columns are included, and restricted columns never get example values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg import sql

from database.allowlist import Allowlist, load_allowlist

RETRIEVAL_DIR = Path(__file__).resolve().parent
DOCS_DIR = RETRIEVAL_DIR / "docs"
NOTES_PATH = RETRIEVAL_DIR / "column_notes.yaml"
DICT_JSON = DOCS_DIR / "data_dictionary.json"
DICT_MD = DOCS_DIR / "data_dictionary.md"

MAX_LISTED_VALUES = 50  # text columns with at most this many distinct values list them all
N_SAMPLES = 3

_COLUMNS_SQL = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

_KEYS_SQL = """
SELECT c.relname, con.contype, a.attname, fc.relname, fa.attname
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(src, dst)
JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.src
LEFT JOIN pg_class fc ON fc.oid = con.confrelid
LEFT JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = k.dst
WHERE n.nspname = %s AND con.contype IN ('p', 'f')
ORDER BY c.relname, con.contype DESC, a.attname
"""

_TEXT_TYPES = ("text", "character varying", "character")


def _fmt(value: Any) -> str:
    return str(value)


def _column_examples(
    conn: psycopg.Connection, schema: str, table: str, column: str, pg_type: str
) -> dict[str, Any]:
    tbl = sql.Identifier(schema, table)
    col = sql.Identifier(column)
    n_distinct = conn.execute(
        sql.SQL("SELECT COUNT(DISTINCT {c}) FROM {t}").format(c=col, t=tbl)
    ).fetchone()[0]
    if pg_type.startswith(_TEXT_TYPES):
        limit = MAX_LISTED_VALUES if n_distinct <= MAX_LISTED_VALUES else N_SAMPLES
        rows = conn.execute(
            sql.SQL("SELECT DISTINCT {c} FROM {t} ORDER BY 1 LIMIT %s").format(c=col, t=tbl),
            (limit,),
        ).fetchall()
        values = [_fmt(r[0]) for r in rows]
        if n_distinct <= MAX_LISTED_VALUES:
            return {"distinct": n_distinct, "values": values}
        return {"distinct": n_distinct, "examples": values}
    lo, hi = conn.execute(
        sql.SQL("SELECT MIN({c}), MAX({c}) FROM {t}").format(c=col, t=tbl)
    ).fetchone()
    return {"distinct": n_distinct, "range": [_fmt(lo), _fmt(hi)]}


def introspect(conn: psycopg.Connection, allowlist: Allowlist | None = None) -> dict[str, Any]:
    """Build the structured dictionary for allow-listed tables/columns."""
    allowlist = allowlist or load_allowlist()
    notes = yaml.safe_load(NOTES_PATH.read_text())
    schema = allowlist.schema

    types: dict[tuple[str, str], tuple[str, bool]] = {
        (t, c): (typ, notnull) for t, c, typ, notnull in conn.execute(_COLUMNS_SQL, (schema,))
    }
    pks: dict[str, list[str]] = {}
    fks: dict[str, list[dict[str, str]]] = {}
    for t, kind, col, ref_t, ref_c in conn.execute(_KEYS_SQL, (schema,)):
        if kind == "p":
            pks.setdefault(t, []).append(col)
        else:
            fks.setdefault(t, []).append({"column": col, "ref_table": ref_t, "ref_column": ref_c})

    tables: dict[str, Any] = {}
    for name, spec in allowlist.tables.items():
        row_count = conn.execute(
            sql.SQL("SELECT COUNT(*) FROM {t}").format(t=sql.Identifier(schema, name))
        ).fetchone()[0]
        columns = []
        for col in spec.columns:
            if (name, col) not in types:
                raise ValueError(f"allow-listed column {name}.{col} not found in the database")
            pg_type, notnull = types[(name, col)]
            entry: dict[str, Any] = {
                "name": col,
                "type": pg_type,
                "nullable": not notnull,
                "meaning": notes[name][col],
                "restricted": col in spec.restricted,
            }
            if col not in spec.restricted:  # never sample personal data
                entry |= _column_examples(conn, schema, name, col, pg_type)
            columns.append(entry)
        tables[name] = {
            "description": spec.description,
            "row_count": row_count,
            "primary_key": pks.get(name, []),
            "foreign_keys": [fk for fk in fks.get(name, []) if fk["ref_table"] in allowlist.tables],
            "columns": columns,
        }
    return {"schema": schema, "tables": tables}


def _examples_text(col: dict[str, Any]) -> str:
    if col["restricted"]:
        return "(restricted — values not shown)"
    if "values" in col:
        return ", ".join(f"'{v}'" for v in col["values"])
    if "examples" in col:
        return "e.g. " + ", ".join(f"'{v}'" for v in col["examples"]) + f" ({col['distinct']} distinct)"
    lo, hi = col["range"]
    return f"{lo} … {hi}"


def table_markdown(name: str, table: dict[str, Any]) -> str:
    """One `## table` section; also used as the table's schema card text."""
    lines = [f"## {name}", "", table["description"], ""]
    lines.append(f"Rows: {table['row_count']:,}. Primary key: {', '.join(table['primary_key']) or '—'}.")
    for fk in table["foreign_keys"]:
        lines.append(f"Join: `{name}.{fk['column']}` → `{fk['ref_table']}.{fk['ref_column']}`.")
    lines += ["", "| column | type | meaning | example values |", "|---|---|---|---|"]
    for col in table["columns"]:
        typ = col["type"] + ("" if col["nullable"] else ", not null")
        meaning = col["meaning"].replace("|", "\\|")
        lines.append(f"| {col['name']} | {typ} | {meaning} | {_examples_text(col)} |")
    return "\n".join(lines)


def render_markdown(data: dict[str, Any]) -> str:
    parts = [
        "# Data dictionary",
        "",
        "<!-- GENERATED by `python -m retrieval.data_dictionary` from the live database. "
        "Do not edit by hand; edit retrieval/column_notes.yaml and regenerate. -->",
        "",
        f"All tables are in schema `{data['schema']}` (synthetic Indian retail data, INR). "
        "Star schema: facts `fact_orders` (one row per order) and `fact_order_items` "
        "(one row per order line); dimensions for date, region, store, customer and product.",
    ]
    for name, table in data["tables"].items():
        parts += ["", table_markdown(name, table)]
    return "\n".join(parts) + "\n"


def write(data: dict[str, Any]) -> None:
    DICT_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    DICT_MD.write_text(render_markdown(data))


def load_dictionary(path: Path = DICT_JSON) -> dict[str, Any]:
    return json.loads(path.read_text())


def main() -> None:
    from app.config import get_settings
    from database.seed import to_conninfo

    with psycopg.connect(to_conninfo(get_settings().database_url_ro.get_secret_value())) as conn:
        conn.read_only = True
        data = introspect(conn)
    write(data)
    print(f"wrote {DICT_JSON.relative_to(RETRIEVAL_DIR.parent)} and {DICT_MD.relative_to(RETRIEVAL_DIR.parent)}")


if __name__ == "__main__":
    main()
