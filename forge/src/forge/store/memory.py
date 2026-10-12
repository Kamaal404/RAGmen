"""In-memory GraphStore for offline testing.

Dict-backed, no database needed. Implements the full GraphStore interface
including entity refcounting on retraction.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from forge.extract.models import ExtractedEntity, ExtractedRelation
from forge.registry.models import Chunk, Source

from .interface import GraphStore


class MemoryStore(GraphStore):
    """Dict-backed store. All data lives in Python dicts."""

    def __init__(self) -> None:
        self.sources: dict[str, Source] = {}
        self.chunks: dict[str, Chunk] = {}
        self.chunks_by_source: dict[str, set[str]] = defaultdict(set)
        self.entities: dict[str, dict[str, Any]] = {}  # entity_id -> data
        self.relations: list[dict[str, Any]] = []
        self.embeddings: dict[str, list[float]] = {}
        self.entity_refs: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )  # entity_id -> {source_id: count}
        self.entity_chunks: dict[str, set[str]] = defaultdict(set)  # entity_id -> {chunk_id}
        self.active_blueprint_hash: str | None = None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def write_chunk(self, chunk: Chunk, source: Source) -> None:
        assert source.source_id is not None
        self.sources[source.source_id] = source  # dedup by source_id
        self.chunks[chunk.chunk_id] = chunk
        self.chunks_by_source[source.source_id].add(chunk.chunk_id)

    def write_entity(
        self,
        entity: ExtractedEntity,
        chunk_id: str,
        source_id: str,
        blueprint_hash: str,
    ) -> None:
        eid = self._entity_id(entity.label, entity.name)
        if eid not in self.entities:
            self.entities[eid] = {
                "label": entity.label,
                "name": entity.name,
                "properties": {p.name: p.value for p in entity.properties},
                "blueprint_hash": blueprint_hash,
                "first_seen_in": source_id,
            }
        self.entity_chunks[eid].add(chunk_id)
        self.entity_refs[eid][source_id] += 1

    def write_relation(
        self,
        relation: ExtractedRelation,
        chunk_id: str,
        blueprint_hash: str,
    ) -> None:
        self.relations.append({
            "source_label": relation.source_label,
            "source_name": relation.source_name,
            "relation": relation.relation,
            "target_label": relation.target_label,
            "target_name": relation.target_name,
            "properties": {p.name: p.value for p in relation.properties},
            "quote": relation.quote,
            "char_start": relation.char_start,
            "char_end": relation.char_end,
            "confidence": relation.confidence,
            "chunk_id": chunk_id,
            "blueprint_hash": blueprint_hash,
        })

    def write_embedding(
        self,
        node_id: str,
        label: str,  # noqa: ARG002
        embedding: list[float],
        blueprint_hash: str,  # noqa: ARG002
    ) -> None:
        self.embeddings[node_id] = embedding

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def create_indexes(self) -> None:
        """No-op for in-memory store."""

    def set_blueprint_active(self, blueprint_hash: str) -> None:
        self.active_blueprint_hash = blueprint_hash

    def get_relations(self) -> list[dict[str, Any]]:
        return list(self.relations)

    def get_source_uri(self, source_id: str) -> str:
        s = self.sources.get(source_id)
        return s.uri if s else ""

    def get_all_source_uris(self) -> dict[str, str]:
        return {sid: s.uri for sid, s in self.sources.items()}

    def get_chunks_by_blueprint(self, blueprint_hash: str) -> list[Chunk]:
        return [c for c in self.chunks.values() if c.blueprint_hash != blueprint_hash]

    def get_entity_ids(self) -> set[str]:
        return set(self.entities.keys())

    def retract_source(self, source_id: str) -> None:
        # Collect chunks for this source
        source_chunks = self.chunks_by_source.get(source_id, set())

        # Find entities that would be orphaned
        orphan_entities: set[str] = set()
        for eid, refs in self.entity_refs.items():
            if source_id in refs and len(refs) == 1:
                # Only referenced by this source
                orphan_entities.add(eid)

        # Remove relations involving orphan entities or their chunks
        self.relations = [
            r for r in self.relations
            if r["chunk_id"] not in source_chunks
        ]

        # Remove orphan entities
        for eid in orphan_entities:
            self.entities.pop(eid, None)
            self.embeddings.pop(eid, None)
            self.entity_chunks.pop(eid, None)

        # Decrement refcounts for surviving entities
        for eid in list(self.entity_refs.keys()):
            if source_id in self.entity_refs[eid]:
                del self.entity_refs[eid][source_id]
                if not self.entity_refs[eid]:
                    del self.entity_refs[eid]

        # Remove chunks
        for cid in source_chunks:
            self.chunks.pop(cid, None)
            self.embeddings.pop(cid, None)
        self.chunks_by_source.pop(source_id, None)

        # Remove or mark source
        if source_id in self.sources:
            import copy as _copy
            s = _copy.deepcopy(self.sources[source_id])
            object.__setattr__(s, "status", "retracted")
            self.sources[source_id] = s

    # ------------------------------------------------------------------
    # Queries (for testing)
    # ------------------------------------------------------------------

    def get_entity(self, name: str, label: str) -> dict[str, Any] | None:
        eid = self._entity_id(label, name)
        return self.entities.get(eid)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self.chunks.get(chunk_id)

    def get_source(self, source_id: str) -> Source | None:
        return self.sources.get(source_id)

    def entity_count(self) -> int:
        return len(self.entities)

    def relation_count(self) -> int:
        return len(self.relations)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.sources.clear()
        self.chunks.clear()
        self.chunks_by_source.clear()
        self.entities.clear()
        self.relations.clear()
        self.embeddings.clear()
        self.entity_refs.clear()
        self.entity_chunks.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _entity_id(label: str, name: str) -> str:
        return f"{label}\0{name}"


__all__ = ["MemoryStore"]