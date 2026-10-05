"""Build the local persistent Chroma index from retrieval/docs/*.md.

    python -m retrieval.build_index
"""

from __future__ import annotations

import re
from pathlib import Path

import chromadb

from app.config import PROJECT_ROOT, get_settings
from retrieval.embedding import embedder_name, get_embedder

DOCS_DIR = Path(__file__).parent / "docs"


def collection_name() -> str:
    return f"eda_docs_{embedder_name()}"


def chunk_markdown(path: Path) -> list[dict]:
    """One chunk per `## ` section, prefixed by the doc title for context."""
    text = path.read_text()
    title = re.search(r"^# (.+)$", text, re.MULTILINE)
    title = title.group(1) if title else path.stem
    chunks = []
    for i, part in enumerate(re.split(r"(?m)^(?=## )", text)):
        part = part.strip()
        if not part or part.startswith("# ") and "\n## " not in part and len(part) < 80:
            continue
        heading = re.match(r"## (.+)", part)
        chunks.append(
            {
                "id": f"{path.stem}:{i}",
                "text": f"[{title}] {part}",
                "meta": {"source": path.name, "section": heading.group(1) if heading else title},
            }
        )
    return chunks


def build_index() -> int:
    chunks = [c for p in sorted(DOCS_DIR.glob("*.md")) for c in chunk_markdown(p)]
    client = chromadb.PersistentClient(path=str(get_settings().chroma_dir))
    name = collection_name()
    try:
        client.delete_collection(name)
    except Exception:
        pass
    col = client.create_collection(name, metadata={"hnsw:space": "cosine"})
    col.add(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=[c["meta"] for c in chunks],
        embeddings=get_embedder().embed([c["text"] for c in chunks]),
    )
    return len(chunks)


if __name__ == "__main__":
    n = build_index()
    print(f"indexed {n} chunks with embedder '{embedder_name()}' into {get_settings().chroma_dir}")
