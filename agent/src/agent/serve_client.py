"""Serve client — direct binding to Serve's retrievers or MCP client.

DirectClient: connects to a MemoryStore or Neo4j-backed retrieve
directly (no running MCP server needed). Used in tests and CLI.
"""

from __future__ import annotations

from typing import Any


class DirectClient:
    """Client that connects directly to a Serve retriever + store.

    No MCP transport needed — ideal for testing and CLI scripts.
    """

    def __init__(
        self,
        store: Any,
        blueprint: dict | None = None,
    ) -> None:
        from serve.retrievers import HybridRetriever

        self._store = store
        self._retriever = HybridRetriever(store)
        self._blueprint = blueprint or {}

    def search(self, query: str, top_k: int = 10, hops: int = 1) -> list[dict]:
        """Search and return hits as plain dicts."""
        result = self._retriever.retrieve(query, top_k=top_k, hops=hops)
        return [
            {
                "chunk_id": h.chunk_id,
                "text": h.text,
                "path": h.path,
                "source_id": h.source_id,
                "page": h.page,
                "score": h.score,
                "entity_names": list(h.entity_names),
            }
            for h in result.hits
        ]

    def cite(self, chunk_id: str) -> dict | None:
        """Get citation details for a chunk."""
        from serve.mcp_server import _format_cite

        chunk = self._store.get_chunk(chunk_id)
        if chunk is None:
            return None
        source = (
            self._store.get_source(chunk.source_id)
            if hasattr(self._store, "get_source")
            else None
        )
        return {
            "chunk_id": chunk_id,
            "path": chunk.path,
            "source_id": chunk.source_id,
            "source_uri": source.uri if source else None,
            "page": chunk.page,
            "text": chunk.text,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
        }

    def schema(self) -> dict:
        ont = self._blueprint.get("ontology", {})
        ap = self._blueprint.get("guardrails", {}).get("answer_policy", {})
        return {"ontology": ont, "answer_policy": ap}

    def expand(self, node_id: str, hops: int = 1, top_k: int = 20) -> list[dict]:
        result = self._retriever.expand(node_id, hops=hops, top_k=top_k)
        return [
            {
                "chunk_id": h.chunk_id,
                "text": h.text,
                "path": h.path,
                "score": h.score,
            }
            for h in result.hits
        ]


__all__ = ["DirectClient"]