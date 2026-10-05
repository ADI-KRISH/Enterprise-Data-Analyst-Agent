"""Build the local ChromaDB index from retrieval/docs/*.md (split by heading) + schema allow-list.

    python -m retrieval.build_index
Embeddings: fastembed (ONNX, local, no API key).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import chromadb
from fastembed import TextEmbedding

from app.config import PROJECT_ROOT, get_settings

DOCS_DIR = PROJECT_ROOT / "retrieval" / "docs"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
COLLECTION = "eda_docs"


@lru_cache
def get_embedder() -> TextEmbedding:
    return TextEmbedding(model_name=EMBED_MODEL)


def embed(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in get_embedder().embed(texts)]


def split_markdown(source: str, text: str) -> list[dict]:
    """One chunk per `## ` section (the `# ` title is prepended for context)."""
    title = re.match(r"#\s+(.+)", text)
    title = title.group(1).strip() if title else source
    chunks = []
    for block in re.split(r"(?m)^(?=## )", text):
        block = block.strip()
        if not block.startswith("## "):
            continue
        heading = block.splitlines()[0][3:].strip()
        chunks.append({"source": source, "heading": heading, "text": f"[{title}] {block}"})
    return chunks


def load_chunks(docs_dir: Path = DOCS_DIR) -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(docs_dir.glob("*.md")):
        chunks.extend(split_markdown(path.stem, path.read_text(encoding="utf-8")))
    return chunks


def build_index(persist_dir: Path | None = None) -> int:
    client = chromadb.PersistentClient(path=str(persist_dir or get_settings().chroma_dir))
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    chunks = load_chunks()
    col.add(
        ids=[f"{c['source']}::{i}" for i, c in enumerate(chunks)],
        documents=[c["text"] for c in chunks],
        embeddings=embed([c["text"] for c in chunks]),
        metadatas=[{"source": c["source"], "heading": c["heading"]} for c in chunks],
    )
    return len(chunks)


if __name__ == "__main__":
    print(f"indexed {build_index()} chunks into {get_settings().chroma_dir}")
