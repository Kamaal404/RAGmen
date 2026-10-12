# ruff: noqa: E501
"""Neo4j adapter for the GraphStore interface.

Every write is wrapped in a single transaction. All operations are idempotent
via MERGE. Blueprint hash is stamped on every node and edge.

Vector and fulltext indexes are created by ``create_indexes()``.
"""

from __future__ import annotations

from typing import Any

from typing import Any

from forge.extract.models import ExtractedEntity, ExtractedRelation
from forge.registry.models import Chunk, Source

from .interface import GraphStore


class Neo4jAdapter(GraphStore):
    """Neo4j 5.x GraphStore adapter.

    Usage::

        with Neo4jAdapter(uri="bolt://localhost:7687", password="s3cret") as store:
            store.write_chunk(chunk, source)
            store.create_indexes()
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        username: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
    ) -> None:
        self._uri = uri
        self._username = username
        self._password = password
        self._database = database
        self._driver: Any = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @property
    def driver(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._username, self._password),
                max_connection_pool_size=10,
            )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def write_chunk(self, chunk: Chunk, source: Source) -> None:
        with self.driver.session(database=self._database) as session:
            session.execute_write(self._tx_write_chunk, chunk, source)

    def write_entity(
        self,
        entity: ExtractedEntity,
        chunk_id: str,
        source_id: str,
        blueprint_hash: str,
    ) -> None:
        with self.driver.session(database=self._database) as session:
            session.execute_write(
                self._tx_write_entity, entity, chunk_id, source_id, blueprint_hash
            )

    def write_relation(
        self,
        relation: ExtractedRelation,
        chunk_id: str,
        blueprint_hash: str,
    ) -> None:
        with self.driver.session(database=self._database) as session:
            session.execute_write(
                self._tx_write_relation, relation, chunk_id, blueprint_hash
            )

    def write_embedding(
        self,
        node_id: str,
        label: str,
        embedding: list[float],
        blueprint_hash: str,  # noqa: ARG002
    ) -> None:
        with self.driver.session(database=self._database) as session:
            session.execute_write(self._tx_write_embedding, node_id, label, embedding)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def create_indexes(self) -> None:
        with self.driver.session(database=self._database) as session:
            session.execute_write(self._tx_create_indexes)

    def set_blueprint_active(self, blueprint_hash: str) -> None:
        with self.driver.session(database=self._database) as session:
            session.execute_write(self._tx_set_blueprint_active, blueprint_hash)

    def get_relations(self) -> list[dict[str, Any]]:
        with self.driver.session(database=self._database) as session:
            rows = session.run("""MATCH ()-[r]->() RETURN r""")
            return [dict(r) for r in rows]

    def get_source_uri(self, source_id: str) -> str:
        with self.driver.session(database=self._database) as session:
            row = session.run("MATCH (s:Source {source_id: $id}) RETURN s.uri", id=source_id).single()
            return row["s.uri"] if row else ""

    def get_all_source_uris(self) -> dict[str, str]:
        with self.driver.session(database=self._database) as session:
            rows = session.run("MATCH (s:Source) RETURN s.source_id, s.uri")
            return {r["s.source_id"]: r["s.uri"] for r in rows}

    def get_chunks_by_blueprint(self, blueprint_hash: str) -> list[Chunk]:
        with self.driver.session(database=self._database) as session:
            rows = session.run("MATCH (c:Chunk {blueprint_hash: $hash}) RETURN c", hash=blueprint_hash)
            return [self._chunk_from_row(r["c"]) for r in rows]

    def get_entity_ids(self) -> set[str]:
        with self.driver.session(database=self._database) as session:
            rows = session.run("MATCH (n) WHERE n:Chunk IS NULL AND n.name IS NOT NULL RETURN n.name, labels(n)")
            return {f"{r['labels(n)'][0]}\0{r['n.name']}" for r in rows}

    def retract_source(self, source_id: str) -> None:
        with self.driver.session(database=self._database) as session:
            session.execute_write(self._tx_retract_source, source_id)

    # ------------------------------------------------------------------
    # Transaction functions (static, no self binding needed)
    # ------------------------------------------------------------------

    @staticmethod
    def _tx_write_chunk(tx: Any, chunk: Chunk, source: Source) -> None:
        tx.run(
            """
            MERGE (s:Source {source_id: $source_id})
            SET s.uri = $uri,
                s.mime = $mime,
                s.blueprint_hash = $bp_hash,
                s.status = $status,
                s.visibility = $visibility
            """,
            source_id=source.source_id,
            uri=source.uri,
            mime=source.mime,
            bp_hash=source.blueprint_hash,
            status=source.status,
            visibility=source.visibility,
        )
        tx.run(
            """
            MERGE (c:Chunk {chunk_id: $chunk_id})
            SET c.path = $path,
                c.text = $text,
                c.char_start = $char_start,
                c.char_end = $char_end,
                c.blueprint_hash = $bp_hash,
                c.page = $page
            """,
            chunk_id=chunk.chunk_id,
            path=chunk.path,
            text=chunk.text,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            bp_hash=chunk.blueprint_hash,
            page=chunk.page,
        )
        tx.run(
            """
            MATCH (s:Source {source_id: $source_id})
            MATCH (c:Chunk {chunk_id: $chunk_id})
            MERGE (s)-[:HAS_CHUNK]->(c)
            """,
            source_id=source.source_id,
            chunk_id=chunk.chunk_id,
        )

    @staticmethod
    def _tx_write_entity(
        tx: Any,
        entity: ExtractedEntity,
        chunk_id: str,
        source_id: str,  # noqa: ARG004
        blueprint_hash: str,
    ) -> None:
        props = {p.name: p.value for p in entity.properties}
        tx.run(
            f"""
            MERGE (e:{entity.label} {{name: $name}})
            SET e += $props,
                e.blueprint_hash = $bp_hash
            """,
            name=entity.name,
            props=props,
            bp_hash=blueprint_hash,
        )
        tx.run(
            """
            MATCH (c:Chunk {chunk_id: $chunk_id})
            MATCH (e {name: $name})
            MERGE (c)-[m:MENTIONS]->(e)
            SET m.quote = $quote,
                m.char_start = $char_start,
                m.char_end = $char_end,
                m.confidence = $confidence,
                m.blueprint_hash = $bp_hash
            """,
            chunk_id=chunk_id,
            name=entity.name,
            quote=entity.quote,
            char_start=entity.char_start,
            char_end=entity.char_end,
            confidence=entity.confidence,
            bp_hash=blueprint_hash,
        )

    @staticmethod
    def _tx_write_relation(
        tx: Any,
        relation: ExtractedRelation,
        chunk_id: str,
        blueprint_hash: str,
    ) -> None:
        props = {p.name: p.value for p in relation.properties}
        tx.run(
            f"""
            MATCH (source:{relation.source_label} {{name: $source_name}})
            MATCH (target:{relation.target_label} {{name: $target_name}})
            MERGE (source)-[r:{relation.relation}]->(target)
            SET r += $props,
                r.chunk_id = $chunk_id,
                r.quote = $quote,
                r.char_start = $char_start,
                r.char_end = $char_end,
                r.confidence = $confidence,
                r.blueprint_hash = $bp_hash
            """,
            source_name=relation.source_name,
            target_name=relation.target_name,
            props=props,
            chunk_id=chunk_id,
            quote=relation.quote,
            char_start=relation.char_start,
            char_end=relation.char_end,
            confidence=relation.confidence,
            bp_hash=blueprint_hash,
        )

    @staticmethod
    def _tx_write_embedding(
        tx: Any, node_id: str, label: str, embedding: list[float]
    ) -> None:
        if label == "Chunk":
            tx.run(
                "MATCH (c:Chunk {chunk_id: $id}) SET c.embedding = $emb",
                id=node_id,
                emb=embedding,
            )
        else:
            tx.run(
                "MATCH (n {name: $name}) SET n.embedding = $emb",
                name=node_id,
                emb=embedding,
            )

    @staticmethod
    def _tx_create_indexes(tx: Any) -> None:
        # Vector index
        tx.run(
            "CREATE VECTOR INDEX chunk_embeddings IF NOT EXISTS "
            "FOR (c:Chunk) ON c.embedding OPTIONS "
            "{indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}}"
        )
        # Fulltext index on chunk text
        tx.run(
            "CREATE FULLTEXT INDEX chunk_text_ft IF NOT EXISTS "
            "FOR (c:Chunk) ON EACH [c.text]"
        )
        # Uniqueness constraints
        tx.run(
            "CREATE CONSTRAINT source_id_unique IF NOT EXISTS "
            "FOR (s:Source) REQUIRE s.source_id IS UNIQUE"
        )
        tx.run(
            "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS "
            "FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE"
        )
        tx.run(
            "CREATE CONSTRAINT chunk_uri_unique IF NOT EXISTS "
            "FOR (s:Source) REQUIRE s.uri IS UNIQUE"
        )

    @staticmethod
    def _tx_set_blueprint_active(tx: Any, blueprint_hash: str) -> None:
        tx.run(
            "MERGE (m:_RagmenMeta) SET m.active_blueprint_hash = $hash",
            hash=blueprint_hash,
        )

    @staticmethod
    def _tx_retract_source(tx: Any, source_id: str) -> None:
        # Find and delete orphan entities (only connected to this source's chunks)
        tx.run(
            """
            MATCH (s:Source {source_id: $sid})<-[:HAS_CHUNK]-(c:Chunk)
            WITH collect(c) AS chunks
            UNWIND chunks AS c
            OPTIONAL MATCH (c)-[m:MENTIONS]->(e)
            WITH chunks, e, count(DISTINCT c) AS _chunk_count
            // For each entity, check if it has MENTIONS from chunks outside this source
            OPTIONAL MATCH (e)<-[:MENTIONS]-(other:Chunk)
            WHERE NOT other IN chunks
            WITH e, other
            WHERE other IS NULL AND e IS NOT NULL
            DETACH DELETE e
            """,
            sid=source_id,
        )
        # Delete the source and its chunks
        tx.run(
            """
            MATCH (s:Source {source_id: $sid})
            OPTIONAL MATCH (s)-[:HAS_CHUNK]->(c:Chunk)
            DETACH DELETE c, s
            """,
            sid=source_id,
        )


__all__ = ["Neo4jAdapter"]