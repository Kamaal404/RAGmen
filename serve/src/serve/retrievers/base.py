"""Base types for all retrievers.

SearchResult carries the output of any retriever so they can be combined
and reranked regardless of the source (vector, fulltext, graph).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single hit from a retrieval operation."""

    chunk_id: str
    text: str
    path: str
    source_id: str | None = None
    page: int | None = None
    score: float = 0.0
    blueprint_hash: str = ""
    char_start: int = 0
    char_end: int = 0
    entity_ids: list[str] = field(default_factory=list)
    entity_labels: list[str] = field(default_factory=list)
    entity_names: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Result of a retrieval operation."""

    query: str
    hits: list[SearchHit] = field(default_factory=list)
    total_hits: int = 0
    retriever: str = "hybrid"


class Retriever:
    """Base class for retrievers.

    Subclasses implement ``retrieve(query, top_k, filters)`` and optionally
    ``retrieve_embedding(vector, top_k, filters)``.
    """

    name: str = "base"

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        """Search by text query."""
        raise NotImplementedError

    def retrieve_vector(
        self,
        embedding: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        """Search by vector embedding."""
        raise NotImplementedError


__all__ = ["Retriever", "SearchHit", "SearchResult"]