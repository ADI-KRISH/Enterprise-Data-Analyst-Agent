"""Tests for the retrieval layer: docs, data dictionary sync, chunking, table selection."""

from pathlib import Path

import pytest
import yaml

from database.allowlist import load_allowlist
from retrieval.build_index import DOCS_DIR, chunk_markdown, load_chunks
from retrieval.data_dictionary import NOTES_PATH, load_dictionary, render_markdown
from retrieval.retriever import connect_tables, _fk_graph

KPIS = {
    "revenue", "gross_margin", "aov", "order_count", "units_sold", "revenue_growth",
    "return_rate", "active_customers", "category_contribution",
}


# ------------------------------------------------------------------ offline checks

def test_column_notes_cover_allowlist_exactly():
    notes = yaml.safe_load(NOTES_PATH.read_text())
    allow = load_allowlist()
    assert {t: list(cols) for t, cols in notes.items()} == {
        t: list(spec.columns) for t, spec in allow.tables.items()
    }


def test_dictionary_matches_allowlist_and_markdown():
    data = load_dictionary()
    allow = load_allowlist()
    assert {t: [c["name"] for c in v["columns"]] for t, v in data["tables"].items()} == {
        t: list(spec.columns) for t, spec in allow.tables.items()
    }
    assert (DOCS_DIR / "data_dictionary.md").read_text() == render_markdown(data)


def test_restricted_columns_have_no_values():
    data = load_dictionary()
    for t, spec in load_allowlist().tables.items():
        for col in data["tables"][t]["columns"]:
            if col["name"] in spec.restricted:
                assert col["restricted"]
                assert not {"values", "examples", "range"} & col.keys()
    assert load_allowlist().tables["dim_customer"].restricted == {"name"}


def test_kpi_chunks_have_names_and_tables():
    known = tuple(load_allowlist().tables)
    chunks = chunk_markdown("kpi_definitions", (DOCS_DIR / "kpi_definitions.md").read_text(), known)
    kpi_chunks = [c for c in chunks if c.kpi_name]
    assert {c.kpi_name for c in kpi_chunks} == KPIS
    for c in kpi_chunks:
        assert c.tables, c.kpi_name
        assert "```sql" in c.text, c.kpi_name
    aov = next(c for c in kpi_chunks if c.kpi_name == "aov")
    assert aov.tables == ("fact_orders", "fact_order_items")


def test_all_chunks_unique_and_dictionary_tables_tagged():
    chunks = load_chunks()
    dict_tables = {c.tables[0] for c in chunks if c.doc == "data_dictionary" and c.tables}
    assert dict_tables == set(load_allowlist().tables)
    assert {c.doc for c in chunks} == {"kpi_definitions", "data_dictionary", "reporting_policies"}


def test_docs_do_not_use_clock_functions():
    for name in ("kpi_definitions.md", "reporting_policies.md"):
        sql_blocks = (DOCS_DIR / name).read_text().split("```sql")[1:]
        for block in sql_blocks:
            body = block.split("```")[0].lower()
            assert "now()" not in body and "current_date" not in body


def test_connect_tables_uses_store_for_region():
    graph = _fk_graph(load_dictionary())
    assert connect_tables({"fact_order_items", "dim_region"}, graph) == {
        "fact_order_items", "fact_orders", "dim_store", "dim_region"
    }
    assert connect_tables({"dim_product"}, graph) == {"dim_product"}


# ------------------------------------------------------------- live DB (optional)

def test_dictionary_in_sync_with_database():
    psycopg = pytest.importorskip("psycopg")
    from app.config import get_settings
    from database.seed import to_conninfo
    from retrieval.data_dictionary import introspect

    try:
        conn = psycopg.connect(to_conninfo(get_settings().database_url_ro.get_secret_value()), connect_timeout=3)
    except Exception as e:  # noqa: BLE001 - DB is optional for this test
        pytest.skip(f"database not reachable: {type(e).__name__}")
    with conn:
        conn.read_only = True
        assert introspect(conn) == load_dictionary(), "run: python -m retrieval.data_dictionary"


# ---------------------------------------------------------- vector index (local)

@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    from retrieval.build_index import build_index, get_embedder
    from retrieval.retriever import Retriever

    try:
        embedder = get_embedder()
    except Exception as e:  # noqa: BLE001 - model download needs network once
        pytest.skip(f"embedding model unavailable: {type(e).__name__}")
    chroma_dir: Path = tmp_path_factory.mktemp("chroma")
    build_index(chroma_dir=chroma_dir, embedder=embedder)
    return Retriever(chroma_dir=chroma_dir, embedder=embedder)


def test_aov_by_category_retrieves_definition_and_tables(retriever):
    r = retriever.retrieve("average order value by category", k=3)
    assert r.kpis[0].kpi_name == "aov"
    assert "COUNT(DISTINCT o.order_id)" in r.kpis[0].text
    assert set(r.table_names) == {"fact_orders", "fact_order_items", "dim_product"}
    product = next(c for c in r.tables if c.table == "dim_product")
    assert "category" in product.columns and "'Furniture'" in product.text


def test_region_question_joins_through_store(retriever):
    r = retriever.retrieve("why did South India revenue drop last quarter?")
    assert r.kpis[0].kpi_name in {"revenue_growth", "revenue"}
    assert {"fact_orders", "fact_order_items", "dim_store", "dim_region"} <= set(r.table_names)
    assert "dim_customer" not in r.table_names
    assert any("ANCHOR_DATE" in p.text for p in r.policies)


def test_customer_card_marks_name_restricted(retriever):
    r = retriever.retrieve("how many active customers in the Corporate segment")
    assert r.kpis[0].kpi_name == "active_customers"
    card = next(c for c in r.tables if c.table == "dim_customer")
    assert card.restricted == ("name",)
    assert "restricted" in card.text.lower()
