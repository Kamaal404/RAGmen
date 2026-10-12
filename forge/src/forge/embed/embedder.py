# ruff: noqa: E501
"""Embedding providers — local sentence-transformers and mock for testing.

Every embedder supports content-hash caching: identical text is never
re-embedded. The cache is an in-memory dict optionally backed by a JSON file
on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Embedder protocol
# ---------------------------------------------------------------------------


class Embedder(ABC):
    """Abstract embedding provider.

    Subclasses must set *model* and *dimensions* as class attributes or
    in __init__.
    """

    model: str = ""
    dimensions: int = 384

    @abstractmethod
    def embed(self, text: str, key: str | None = None) -> list[float]:
        """Return a fixed-size embedding vector for *text*.

        When *key* is provided and cached, return the cached vector.
        """

    @abstractmethod
    def embed_batch(
        self,
        texts: list[str],
        keys: list[str | None] | None = None,
    ) -> list[list[float]]:
        """Return embeddings for a batch of texts (more efficient than loop)."""


# ---------------------------------------------------------------------------
# Mock (deterministic, no dependencies)
# ---------------------------------------------------------------------------


class MockEmbedder(Embedder):
    """Deterministic mock for testing.

    Produces a fixed-dimension vector of alternating ``0.1, -0.1, 0.1, ...``
    seeded by SHA-256 of the input text. Every embedder test uses this;
    no sentence-transformers dependency needed.
    """

    model = "mock"
    dimensions: int

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed(self, text: str, key: str | None = None) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            (0.1 if (seed[i % 32] + (i // 32)) % 2 == 0 else -0.1)
            for i in range(self.dimensions)
        ]

    def embed_batch(
        self,
        texts: list[str],
        keys: list[str | None] | None = None,  # noqa: ARG002
    ) -> list[list[float]]:
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# Local (sentence-transformers)
# ---------------------------------------------------------------------------

_LOCAL_AVAILABLE: bool | None = None


def _local_import() -> bool:
    """Lazy-import sentence-transformers once."""
    global _LOCAL_AVAILABLE
    if _LOCAL_AVAILABLE is not None:
        return _LOCAL_AVAILABLE
    try:
        import sentence_transformers  # noqa: F401
        _LOCAL_AVAILABLE = True
    except ImportError:
        _LOCAL_AVAILABLE = False
    return _LOCAL_AVAILABLE


class LocalEmbedder(Embedder):
    """sentence-transformers local embedding.

    The model is loaded lazily on the first call to ``embed`` or
    ``embed_batch``, so importing this module does not trigger a model
    download.

    Cache is stored in memory and optionally persisted to
    ``{cache_dir}/embedding_cache.json``.
    """

    model: str
    dimensions: int
    _CACHE_FILE = "embedding_cache.json"

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        cache_dir: str | Path | None = None,
    ) -> None:
        self.model_name = model_name
        self.model = model_name
        self.dimensions = 384  # all-MiniLM-L6-v2
        self._model: Any = None
        self._cache: dict[str, list[float]] = {}
        self._cache_dir: Path | None = Path(cache_dir) if cache_dir else None
        self._dirty = False

        # Load persisted cache
        if self._cache_dir:
            cache_path = self._cache_dir / self._CACHE_FILE
            if cache_path.exists():
                try:
                    raw = cache_path.read_text(encoding="utf-8")
                    self._cache = {
                        k: [float(v) for v in vec]
                        for k, vec in json.loads(raw).items()
                    }
                except (json.JSONDecodeError, ValueError):
                    self._cache = {}

    def _load_model(self) -> None:
        if self._model is not None:
            return
        if not _local_import():
            raise ImportError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            )
        # pylint: disable-next=import-outside-toplevel
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)

    def _persist(self) -> None:
        if not self._cache_dir or not self._dirty:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_dir / self._CACHE_FILE
        cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False), encoding="utf-8"
        )

    def _content_key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def embed(self, text: str, key: str | None = None) -> list[float]:
        cache_key = key or self._content_key(text)
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._load_model()
        vec = self._model.encode(text, normalize_embeddings=True).tolist()
        self._cache[cache_key] = vec
        self._dirty = True
        self._persist()
        return vec

    def embed_batch(
        self,
        texts: list[str],
        keys: list[str | None] | None = None,
    ) -> list[list[float]]:
        results: list[list[float]] = []
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            cache_key = (keys[i] if keys else None) or self._content_key(text)
            if cache_key in self._cache:
                results.append(self._cache[cache_key])
            else:
                results.append([])  # placeholder
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            self._load_model()
            embeddings = self._model.encode(
                uncached_texts, normalize_embeddings=True
            ).tolist()
            for idx, vec in zip(uncached_indices, embeddings):
                text = texts[idx]
                cache_key = (
                    (keys[idx] if keys else None) or self._content_key(text)
                )
                self._cache[cache_key] = vec
                results[idx] = vec
            self._dirty = True
            self._persist()

        return results


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def embed_for(backend: str = "local", **kwargs: Any) -> Embedder:
    """Return the right Embedder for the backend name."""
    if backend == "mock":
        return MockEmbedder(**kwargs)
    if backend == "local":
        return LocalEmbedder(**kwargs)
    raise ValueError(f"Unknown embedder backend: {backend!r}. Options: mock, local")


# ---------------------------------------------------------------------------
# Blueprint-driven target builder
# ---------------------------------------------------------------------------


def build_embedding_targets(
    blueprint: dict,
    chunks: list[dict],
    resolved_entities: list | None = None,
) -> list[tuple[str, str, str]]:
    """Build (target_name, text, content_hash) tuples from blueprint targets.

    Reads ``blueprint.embedding.targets`` and formats the text template
    with available fields. Chunk targets use chunk data; entity targets
    use resolved entity data.

    Returns:
        List of (target_name, rendered_text, content_hash).
    """
    targets: list[tuple[str, str, str]] = []
    embedding_cfg = blueprint.get("embedding", {})
    target_defs = embedding_cfg.get("targets", [])

    # Build a lookup of resolved entities by label => name
    entity_map: dict[str, dict[str, Any]] = {}
    if resolved_entities:
        for re_entity in resolved_entities:
            label = re_entity.label
            if label not in entity_map:
                entity_map[label] = {}
            entity_map[label][re_entity.canonical_name] = re_entity

    for tdef in target_defs:
        of_types = tdef.get("of", [])
        if isinstance(of_types, str):
            of_types = [of_types]

        # Determine if this target applies to chunks or entities
        is_chunk = "Chunk" in of_types
        is_entity = any(t not in ("Chunk",) for t in of_types)
        template = tdef.get("text", "{text}")

        if is_chunk:
            for chunk in chunks:
                text = chunk.get("text", "")
                path = chunk.get("path", "")
                rendered = template.format(path=path, text=text)
                chunk_key = f"chunk:{chunk.get('chunk_id', '')}"
                targets.append((tdef.get("name", "chunk"), rendered, chunk_key))

        if is_entity and resolved_entities:
            entity_types = [t for t in of_types if t != "Chunk"]
            for re_entity in resolved_entities:
                if entity_types and re_entity.label not in entity_types:
                    continue
                # Build a formatting dict from the entity
                fmt: dict[str, str] = {
                    "name": re_entity.canonical_name,
                }
                rendered = template.format(**fmt)
                ekey = f"entity:{re_entity.entity_id}"
                targets.append((tdef.get("name", "entity"), rendered, ekey))

    return targets


__all__ = [
    "Embedder",
    "MockEmbedder",
    "LocalEmbedder",
    "build_embedding_targets",
    "embed_for",
]