"""Hybrid retriever — vector similarity + fulltext keyword + graph traversal.

Strategy:
  1. Vector search — cosine similarity against chunk embeddings
  2. Fulltext keyword search — against chunk text
  3. Graph traversal — expand seed hits through their entity neighbourhood
  4. Fusion — Reciprocal Rank Fusion of the three result lists

In MemoryStore mode, all operations run against Python dicts. In Neo4j mode,
they run as Cypher queries on the database.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .base import Retriever, SearchHit, SearchResult


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(ai * bi for ai, bi in zip(a, b))
    na = math.sqrt(sum(ai * ai for ai in a))
    nb = math.sqrt(sum(bi * bi for bi in b))
    return dot / (na * nb + 1e-10)


def _rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal rank fusion score for a hit at position *rank* (0-indexed)."""
    return 1.0 / (k + rank + 1)


def _rrf_fuse(
    *lists: list[SearchHit],
    top_k: int,
) -> list[SearchHit]:
    """Fuse multiple ranked lists via Reciprocal Rank Fusion."""
    scores: dict[str, tuple[float, SearchHit]] = {}
    for ranked in lists:
        for rank, hit in enumerate(ranked):
            if hit.chunk_id in scores:
                old_score, old_hit = scores[hit.chunk_id]
                scores[hit.chunk_id] = (
                    old_score + _rrf_score(rank),
                    old_hit,
                )
            else:
                scores[hit.chunk_id] = (_rrf_score(rank), hit)

    sorted_hits = sorted(
        scores.values(), key=lambda x: x[0], reverse=True
    )
    result = []
    for score, hit in sorted_hits[:top_k]:
        # Swap the per-list score with the fused score
        new_hit = SearchHit(
            chunk_id=hit.chunk_id,
            text=hit.text,
            path=hit.path,
            source_id=hit.source_id,
            page=hit.page,
            score=round(score, 4),
            blueprint_hash=hit.blueprint_hash,
            char_start=hit.char_start,
            char_end=hit.char_end,
            entity_ids=list(hit.entity_ids),
            entity_labels=list(hit.entity_labels),
            entity_names=list(hit.entity_names),
        )
        result.append(new_hit)
    return result


# ---- MemoryStore-backed retrievers ----


class _MemoryVectorRetriever:
    """Vector similarity search against a MemoryStore."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def retrieve(
        self, query_emb: list[float], top_k: int = 10
    ) -> list[SearchHit]:
        scores: list[tuple[float, str]] = []
        for cid, emb in self.store.embeddings.items():
            sim = _cosine_similarity(query_emb, emb)
            scores.append((sim, cid))
        scores.sort(key=lambda x: x[0], reverse=True)

        hits: list[SearchHit] = []
        for sim, cid in scores[:top_k]:
            chunk = self.store.get_chunk(cid)
            if chunk is None:
                continue
            source = self.store.get_source(chunk.source_id)
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text[:500],
                    path=chunk.path,
                    source_id=chunk.source_id,
                    page=chunk.page,
                    score=round(sim, 4),
                    blueprint_hash=chunk.blueprint_hash,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    entity_ids=list(
                        eid for eid, chunks in self.store.entity_chunks.items()
                        if chunk.chunk_id in chunks
                    ),
                )
            )
        return hits


class _MemoryFulltextRetriever:
    """Simple keyword search against a MemoryStore."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def retrieve(
        self, query: str, top_k: int = 10
    ) -> list[SearchHit]:
        terms = query.lower().split()
        scores: list[tuple[int, str]] = []
        for cid, chunk in self.store.chunks.items():
            text_lower = chunk.text.lower()
            score = sum(term in text_lower for term in terms)
            if score > 0:
                scores.append((score, cid))
        scores.sort(key=lambda x: x[0], reverse=True)

        hits: list[SearchHit] = []
        for score, cid in scores[:top_k]:
            chunk = self.store.chunks[cid]
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text[:500],
                    path=chunk.path,
                    source_id=chunk.source_id,
                    page=chunk.page,
                    score=round(score / len(terms), 4),
                    blueprint_hash=chunk.blueprint_hash,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
            )
        return hits


class _MemoryGraphRetriever:
    """Graph traversal — expand seed chunks through entity neighbourhood."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._entity_to_chunks: dict[str, set[str]] = {}
        self._chunk_to_entities: dict[str, set[str]] = {}
        self._build_index()

    def _build_index(self) -> None:
        for eid, chunks in self.store.entity_chunks.items():
            self._entity_to_chunks[eid] = chunks
            for cid in chunks:
                self._chunk_to_entities.setdefault(cid, set()).add(eid)

    def retrieve(
        self,
        seed_chunk_ids: list[str],
        hops: int = 1,
        top_k: int = 10,
    ) -> list[SearchHit]:
        visited_chunks: set[str] = set(seed_chunk_ids)
        current: set[str] = set(seed_chunk_ids)

        for _ in range(hops):
            entities: set[str] = set()
            for cid in current:
                entities.update(self._chunk_to_entities.get(cid, set()))
            neighbours: set[str] = set()
            for eid in entities:
                neighbours.update(self._entity_to_chunks.get(eid, set()))
            current = neighbours - visited_chunks
            visited_chunks.update(current)

        # Exclude seeds, return top_k neighbours
        result_chunks = list(visited_chunks - set(seed_chunk_ids))

        hits: list[SearchHit] = []
        for cid in result_chunks[:top_k]:
            chunk = self.store.get_chunk(cid)
            if chunk is None:
                continue
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text[:500],
                    path=chunk.path,
                    source_id=chunk.source_id,
                    page=chunk.page,
                    score=1.0 / (1 + len(hits)),
                    blueprint_hash=chunk.blueprint_hash,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
            )
        return hits


# ---- Hybrid retriever ----


class HybridRetriever(Retriever):
    """Hybrid retriever combining vector, fulltext, and graph.

    Usage::

        retriever = HybridRetriever(store)
        result = retriever.retrieve("what is a dakota fire hole", top_k=10)
    """

    name = "hybrid"

    def __init__(
        self,
        store: Any,
        embed_query: Callable[[str], list[float]] | None = None,
    ) -> None:
        self.store = store
        # The query must be embedded by the same model that embedded the
        # chunks, or cosine similarity compares unrelated spaces. Without one
        # this falls back to the hash mock, which only matches MockEmbedder.
        self._embed_query = embed_query
        # Always try to build memory-mode sub-retrievers; they degrade
        # gracefully if embeddings or entity index are missing.
        self._vector = _MemoryVectorRetriever(store)
        self._fulltext = _MemoryFulltextRetriever(store)
        self._graph = _MemoryGraphRetriever(store)

    def _get_query_embedding(self, query: str) -> list[float]:
        """Produce a mock embedding for the query.

        In production this calls the same embedder used in Forge.
        For now, produce a deterministic mock embedding so the
        vector retriever works without sentence-transformers.
        """
        if self._embed_query is not None:
            return self._embed_query(query)

        import hashlib

        seed = hashlib.sha256(query.encode("utf-8")).digest()
        return [
            (0.1 if (seed[i % 32] + (i // 32)) % 2 == 0 else -0.1)
            for i in range(384)
        ]

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,  # noqa: ARG002
        hops: int = 1,
    ) -> SearchResult:
        """Hybrid search: vector + fulltext fused, then graph-expanded.

        Strategy:
        1. Vector search finds semantically similar chunks
        2. Fulltext search finds keyword-matching chunks
        3. RRF fuse the two result lists
        4. Graph-traverse from the fused hits to find neighbours
        5. RRF fuse again with the traversal results
        """
        # 1. Vector
        query_emb = self._get_query_embedding(query)
        vector_hits = self._vector.retrieve(query_emb, top_k=top_k * 2)

        # 2. Fulltext
        fulltext_hits = self._fulltext.retrieve(query, top_k=top_k * 2)

        # 3. Fuse vector + fulltext
        fused = _rrf_fuse(vector_hits, fulltext_hits, top_k=top_k)

        # 4. Graph traversal from fused seed chunks
        seed_ids = [h.chunk_id for h in fused[:max(3, top_k // 2)]]
        graph_hits = self._graph.retrieve(seed_ids, hops=hops, top_k=top_k)

        # 5. Final fusion
        final = _rrf_fuse(fused, graph_hits, top_k=top_k)

        return SearchResult(
            query=query,
            hits=final,
            total_hits=len(final),
            retriever=self.name,
        )

    def retrieve_vector(
        self,
        embedding: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> SearchResult:
        """Vector similarity alone: the plain-RAG baseline the graph must beat."""
        hits = self._vector.retrieve(embedding, top_k=top_k)
        return SearchResult(query="", hits=hits, total_hits=len(hits), retriever="vector")

    def expand(
        self,
        node_id: str,
        hops: int = 1,
        top_k: int = 20,
    ) -> SearchResult:
        """Neighbourhood expansion from a chunk or entity.

        If *node_id* is a chunk_id, finds entities mentioned in that chunk
        then their other chunks. If it's an entity_id, finds chunks that
        mention it then their other entities.
        """
        # Try as chunk first
        chunk = self.store.get_chunk(node_id)
        if chunk is not None:
            seeds = [node_id]
        elif hasattr(self.store, "get_entity") and node_id in (
            getattr(self.store, "entities") or {}
        ):
            seeds = list(
                self.store.entity_chunks.get(node_id, [])
            )
        else:
            # Treat node_id as a chunk_id even if not found
            seeds = [node_id]

        graph_hits = self._graph.retrieve(seeds, hops=hops, top_k=top_k)
        return SearchResult(
            query=f"expand:{node_id}",
            hits=graph_hits,
            total_hits=len(graph_hits),
            retriever="graph",
        )


__all__ = ["HybridRetriever"]