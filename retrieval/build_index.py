"""Build the local vector index over retrieval/docs/*.md.

Usage:  python -m retrieval.build_index

Each markdown file is chunked by `## ` heading (the text before the first `## ` is an
"Overview" chunk), embedded locally with fastembed, and stored in a persistent Chroma
collection with metadata {doc, section, kpi_name, tables}. Rebuilding replaces the
collection, so the command is idempotent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastembed import TextEmbedding

from database.allowlist import load_allowlist

DOCS_DIR = Path(__file__).resolve().parent / "docs"
COLLECTION = "knowledge_base"

_H2 = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
_KPI_NAME = re.compile(r"^\*\*kpi_name:\*\*\s*([a-z0-9_]+)", re.MULTILINE)
_TABLES = re.compile(r"^\*\*Tables:\*\*\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    id: str
    doc: str
    section: str
    text: str
    kpi_name: str  # "" when the chunk is not a KPI definition
    tables: tuple[str, ...]

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "doc": self.doc,
            "section": self.section,
            "kpi_name": self.kpi_name,
            "tables": ",".join(self.tables),
        }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _tables_in(text: str, known: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(t for t in known if re.search(rf"\b{t}\b", text))


def chunk_markdown(doc: str, markdown: str, known_tables: tuple[str, ...]) -> list[Chunk]:
    """Split one markdown document into heading chunks with metadata."""
    heads = list(_H2.finditer(markdown))
    sections: list[tuple[str, str]] = []
    preamble = markdown[: heads[0].start() if heads else len(markdown)].strip()
    if preamble:
        sections.append(("Overview", preamble))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(markdown)
        sections.append((h.group(1), markdown[h.start():end].strip()))

    chunks = []
    for section, text in sections:
        kpi = _KPI_NAME.search(text)
        if kpi:  # KPI chunks declare their tables explicitly
            tables_line = _TABLES.search(text)
            tables = _tables_in(tables_line.group(1), known_tables) if tables_line else ()
        elif doc == "data_dictionary" and section in known_tables:
            tables = (section,)
        else:
            tables = ()
        chunks.append(Chunk(
            id=f"{doc}:{_slug(section)}",
            doc=doc,
            section=section,
            text=text,
            kpi_name=kpi.group(1) if kpi else "",
            tables=tables,
        ))
    return chunks


def load_chunks(docs_dir: Path = DOCS_DIR) -> list[Chunk]:
    known = tuple(load_allowlist().tables)
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        chunks += chunk_markdown(path.stem, path.read_text(), known)
    ids = [c.id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids; section headings must be unique per document")
    return chunks


def embedding_text(chunk: Chunk) -> str:
    """Text that is embedded: the section text prefixed with its document for context."""
    return f"{chunk.doc.replace('_', ' ')} — {chunk.section}\n{chunk.text}"


def get_embedder(model: str | None = None, cache_dir: Path | None = None) -> TextEmbedding:
    from app.config import get_settings

    s = get_settings()
    return TextEmbedding(
        model_name=model or s.embed_model, cache_dir=str(cache_dir or s.embed_cache_dir)
    )


def open_client(chroma_dir: Path) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(
        path=str(chroma_dir), settings=ChromaSettings(anonymized_telemetry=False)
    )


def build_index(
    chroma_dir: Path | None = None,
    docs_dir: Path = DOCS_DIR,
    embedder: TextEmbedding | None = None,
) -> int:
    """(Re)build the collection; returns the number of chunks indexed."""
    from app.config import get_settings

    chroma_dir = chroma_dir or get_settings().chroma_dir
    embedder = embedder or get_embedder()
    chunks = load_chunks(docs_dir)
    vectors = [v.tolist() for v in embedder.passage_embed([embedding_text(c) for c in chunks])]

    client = open_client(chroma_dir)
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        COLLECTION, embedding_function=None, metadata={"hnsw:space": "cosine"}
    )
    collection.add(
        ids=[c.id for c in chunks],
        documents=[c.text for c in chunks],
        embeddings=vectors,
        metadatas=[c.metadata for c in chunks],
    )
    return len(chunks)


def main() -> None:
    n = build_index()
    print(f"indexed {n} chunks into collection '{COLLECTION}'")


if __name__ == "__main__":
    main()
