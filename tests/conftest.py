import os

os.environ.setdefault("EMBEDDER", "hash")  # offline, deterministic embeddings in tests
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
