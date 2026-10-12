"""Embedding module — vector generation with content-hash caching.

Usage::

    embedder = embed_for("local", model_name="all-MiniLM-L6-v2")
    vector = embedder.embed("some text", key="content_hash")
    vectors = embedder.embed_batch([("text1", "key1"), ("text2", "key2")])
"""

from .embedder import Embedder, MockEmbedder, build_embedding_targets, embed_for

__all__ = ["Embedder", "MockEmbedder", "build_embedding_targets", "embed_for"]