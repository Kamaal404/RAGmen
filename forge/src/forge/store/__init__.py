"""Store module — abstract GraphStore + Neo4j + Memory adapters.

Factory::

    store = store_for("memory")               # offline testing
    store = store_for("neo4j", uri=..., ...)  # production
"""

from __future__ import annotations

from typing import Any

from .interface import GraphStore
from .memory import MemoryStore


def store_for(backend: str = "memory", **kwargs: Any) -> GraphStore:
    """Return the right GraphStore for the backend name."""
    if backend == "neo4j":
        from .neo4j import Neo4jAdapter

        return Neo4jAdapter(**kwargs)
    if backend == "memory":
        return MemoryStore(**kwargs)
    raise ValueError(f"Unknown store backend: {backend!r}. Options: memory, neo4j")


__all__ = ["GraphStore", "MemoryStore", "store_for"]