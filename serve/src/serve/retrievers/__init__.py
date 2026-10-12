"""Retrievers for Serve — vector, fulltext, graph traversal, and hybrid search.

All retrievers operate on a store backend. In memory mode they run against
the MemoryStore dicts; in Neo4j mode they run Cypher queries directly.
"""

from .base import SearchResult, SearchHit, Retriever
from .hybrid import HybridRetriever

__all__ = ["SearchHit", "SearchResult", "Retriever", "HybridRetriever"]