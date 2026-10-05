"""Schema + KPI retrieval (RAG).

The schema is tiny (7 tables), so the full compact schema is always supplied; search over the KPI definitions,
data dictionary and reporting policies adds the relevant definitions for the question.

Backends (RETRIEVAL_BACKEND): `vector` = ChromaDB + fastembed; `lexical` = TF-IDF keyword scoring with no model
download; `auto` (default) = vector, falling back to lexical if the embedding model cannot be loaded.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from functools import lru_cache
from typing import Any

from agents.sql_validator import load_allowlist
from app.config import get_settings
from retrieval.build_index import COLLECTION, load_chunks

log = logging.getLogger(__name__)
_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were", "by", "what",
         "which", "how", "did", "do", "does", "our", "we", "my", "me", "show", "give", "from", "with", "at", "it"}


def schema_summary() -> str:
    return "\n".join(f"analytics.{t}({', '.join(cols)})" for t, cols in load_allowlist().items())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@lru_cache
def _lexical_index() -> tuple[list[dict], list[Counter], dict[str, float]]:
    chunks = load_chunks()
    docs = [Counter(_tokens(c["heading"] + " " + c["heading"] + " " + c["text"])) for c in chunks]
    df: Counter = Counter(t for d in docs for t in d)
    idf = {t: math.log(1 + len(docs) / n) for t, n in df.items()}
    return chunks, docs, idf


def retrieve_lexical(question: str, k: int = 5) -> list[dict]:
    chunks, docs, idf = _lexical_index()
    q = set(_tokens(question))
    scored = []
    for chunk, doc in zip(chunks, docs):
        norm = math.sqrt(sum(doc.values())) or 1.0
        score = sum(idf.get(t, 0) * (1 + math.log(doc[t])) for t in q if t in doc) / norm
        scored.append((score, chunk))
    scored.sort(key=lambda x: -x[0])
    return [{**c, "score": round(s, 4)} for s, c in scored[:k] if s > 0]


def retrieve_vector(question: str, k: int = 5) -> list[dict]:
    import chromadb

    from retrieval.build_index import build_index, embed

    client = chromadb.PersistentClient(path=str(get_settings().chroma_dir))
    try:
        col = client.get_collection(COLLECTION)
        if col.count() == 0:
            raise ValueError("empty")
    except Exception:
        build_index()
        col = client.get_collection(COLLECTION)
    res = col.query(query_embeddings=embed([question]), n_results=min(k, col.count()))
    return [
        {"source": m["source"], "heading": m["heading"], "text": doc, "score": round(1 - dist, 4)}
        for doc, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def retrieve(question: str, k: int = 5) -> dict[str, Any]:
    backend = get_settings().retrieval_backend
    chunks: list[dict] = []
    used = "lexical"
    if backend in ("vector", "auto"):
        try:
            chunks, used = retrieve_vector(question, k), "vector"
        except Exception as exc:
            if backend == "vector":
                raise
            log.warning("vector retrieval unavailable (%s); using lexical search", exc)
    if used == "lexical":
        chunks = retrieve_lexical(question, k)
    return {"schema": schema_summary(), "chunks": chunks, "backend": used}


def format_context(ctx: dict[str, Any]) -> str:
    docs = "\n\n".join(c["text"] for c in ctx.get("chunks", []))
    return f"SCHEMA:\n{ctx.get('schema', '')}\n\nRELEVANT DEFINITIONS AND POLICIES:\n{docs}"
