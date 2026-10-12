"""Abstract graph+vector store interface for Lattice Forge.

The contract between Forge's deterministic pipeline and whatever storage
backend (Neo4j, in-memory for tests). Forge never makes ontology decisions;
it writes what extraction and resolution produce.

Every write operation stamps blueprint_hash on the affected nodes/edges so
a later blueprint change can target exactly what it invalidates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType

from typing import Any

from forge.extract.models import ExtractedEntity, ExtractedRelation
from forge.registry.models import Chunk, Source


class GraphStore(ABC):
    """Abstract graph+vector store.

    One write transaction per method call. Callers that need atomicity across
    multiple writes manage it at the pipeline level (write-then-verify).
    """

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    @abstractmethod
    def write_chunk(self, chunk: Chunk, source: Source) -> None:
        """Persist a Chunk node and link it to its Source.

        MERGE on chunk_id so re-ingest is idempotent.
        """

    @abstractmethod
    def write_entity(
        self,
        entity: ExtractedEntity,
        chunk_id: str,
        source_id: str,
        blueprint_hash: str,
    ) -> None:
        """Persist an entity node and a MENTIONS edge from its chunk.

        MERGE on (label, name) — two chunks that mention the same real-world
        thing converge on the same node. Stamps blueprint_hash.
        """

    @abstractmethod
    def write_relation(
        self,
        relation: ExtractedRelation,
        chunk_id: str,
        blueprint_hash: str,
    ) -> None:
        """Persist a typed relationship between two entities.

        MERGE both endpoints by (label, name), then MERGE the relationship
        with provenance (chunk_id, quote, confidence).
        """

    @abstractmethod
    def write_embedding(
        self,
        node_id: str,
        label: str,
        embedding: list[float],
        blueprint_hash: str,
    ) -> None:
        """Attach a vector embedding to an existing node."""

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    @abstractmethod
    def create_indexes(self) -> None:
        """Create vector index, fulltext index, uniqueness constraints."""

    @abstractmethod
    def set_blueprint_active(self, blueprint_hash: str) -> None:
        """Stamp the active blueprint hash on the singleton _RagmenMeta node."""

    @abstractmethod
    def retract_source(self, source_id: str) -> None:
        """Remove a source and its data with entity refcounting.

        Entities that are only mentioned by the retracted source's chunks
        are deleted. Entities shared with other sources survive.
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Queries (needed by Serve, agent, and conflict detection)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_relations(self) -> list[dict[str, Any]]:
        """Return all relations with their source provenance."""

    @abstractmethod
    def get_source_uri(self, source_id: str) -> str:
        """Return the URI for a source."""

    @abstractmethod
    def get_all_source_uris(self) -> dict[str, str]:
        """Return dict of source_id -> uri."""

    @abstractmethod
    def get_chunks_by_blueprint(self, blueprint_hash: str) -> list[Chunk]:
        """Return chunks stamped with this blueprint_hash."""

    @abstractmethod
    def get_entity_ids(self) -> set[str]:
        """Return all known entity IDs."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def close(self) -> None:
        """Release backend resources."""

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["GraphStore"]