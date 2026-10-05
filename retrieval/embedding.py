"""Embedding functions for the Chroma index.

Default: fastembed (local ONNX model, no API key; the model file is downloaded from
Hugging Face on first use). If that is unavailable (e.g. offline / egress-blocked),
fall back to a deterministic hashed bag-of-words embedder so retrieval still works
(lexical, lower quality). Which one is active is recorded in the collection name and
returned by `embedder_name()`.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from functools import lru_cache

FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
HASH_DIM = 384
_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = {"the", "a", "an", "of", "in", "to", "and", "or", "is", "are", "for", "by", "on", "what",
         "how", "was", "were", "with", "as", "at", "it", "this", "that", "me", "show"}


def _tokens(text: str) -> list[str]:
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


class HashEmbedder:
    name = "hash"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * HASH_DIM
            for tok in _tokens(text):
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
                vec[h % HASH_DIM] += 1.0 if (h >> 63) & 1 else -1.0
                vec[(h >> 20) % HASH_DIM] += 0.5
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FastEmbedder:
    name = "fastembed"

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(FASTEMBED_MODEL)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]


@lru_cache
def get_embedder():
    """EMBEDDER=fastembed|hash|auto (default auto: fastembed, else hash)."""
    choice = os.environ.get("EMBEDDER", "auto").lower()
    if choice == "hash":
        return HashEmbedder()
    try:
        return FastEmbedder()
    except Exception:
        if choice == "fastembed":
            raise
        return HashEmbedder()


def embedder_name() -> str:
    return get_embedder().name
