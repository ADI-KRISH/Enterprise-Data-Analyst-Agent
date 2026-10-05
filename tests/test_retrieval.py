import os

os.environ["EMBEDDER"] = "hash"  # offline, deterministic

from retrieval.build_index import build_index, chunk_markdown, DOCS_DIR  # noqa: E402
from retrieval.retriever import retrieve, schema_summary  # noqa: E402


def test_chunks_have_sections():
    chunks = chunk_markdown(DOCS_DIR / "kpi_definitions.md")
    assert len(chunks) > 8 and all(c["text"].startswith("[KPI") for c in chunks)


def test_retrieves_relevant_kpi(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path))
    from app.config import get_settings
    get_settings.cache_clear()
    import retrieval.retriever as r
    r._collection.cache_clear()
    build_index()
    hits = retrieve("what is the return rate by category", k=3)
    assert any("Return rate" in h["text"] for h in hits)
    hits = retrieve("quarter fiscal last quarter relative date", k=3)
    assert any("ANCHOR_DATE" in h["text"] or "Fiscal" in h["text"] for h in hits)
    get_settings.cache_clear()


def test_schema_summary():
    assert "fact_orders(" in schema_summary()
