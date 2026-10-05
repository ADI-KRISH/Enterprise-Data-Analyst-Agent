"""Schema + KPI retrieval (RAG) over the Chroma index."""

from __future__ import annotations

from functools import lru_cache

import chromadb

from agents.sql_validator import load_allowlist
from app.config import get_settings
from retrieval.build_index import build_index, collection_name
from retrieval.embedding import embedder_name, get_embedder


@lru_cache
def _collection():
    client = chromadb.PersistentClient(path=str(get_settings().chroma_dir))
    try:
        return client.get_collection(collection_name())
    except Exception:
        build_index()
        return client.get_collection(collection_name())


def retrieve(question: str, k: int = 6) -> list[dict]:
    col = _collection()
    res = col.query(
        query_embeddings=get_embedder().embed([question]), n_results=min(k, col.count())
    )
    return [
        {"text": d, "source": m["source"], "section": m["section"], "distance": dist}
        for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def schema_summary() -> str:
    """Compact, always-included schema listing generated from the allow-list."""
    allow = load_allowlist()
    lines = [f"Schema `{allow['schema']}` (PostgreSQL). Tables and columns:"]
    for table, cols in allow["tables"].items():
        lines.append(f"- {table}({', '.join(cols)})")
    return "\n".join(lines)


def format_context(chunks: list[dict]) -> str:
    return "\n\n".join(f"({c['source']} — {c['section']})\n{c['text']}" for c in chunks)


__all__ = ["retrieve", "schema_summary", "format_context", "embedder_name"]
