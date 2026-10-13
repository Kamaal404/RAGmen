# ruff: noqa: E501, S101
"""Tests for Forge phase 3: resolve, embed, store, pipeline, drift.

All tests run offline with no API key or database connection.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from forge.embed import MockEmbedder, build_embedding_targets, embed_for
from forge.extract.models import (
    ExtractedEntity,
    ExtractedProperty,
    ExtractedRelation,
    ExtractionResult,
)
from forge.pipeline import PipelineResult, run_pipeline, run_pipeline_report
from forge.registry.models import Chunk, Source
from forge.report import DriftReport, build_drift_report, print_report
from forge.resolve import EntityResolver, ResolvedEntity, Vocabulary, resolve_batch
from forge.store import MemoryStore, store_for


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_BLUEPRINT = {
    "name": "test",
    "blueprint_version": "1.0.0",
    "blueprint_hash": "sha256:test123",
    "ontology": {
        "closed": True,
        "node_types": [
            {
                "label": "Material",
                "description": "Something consumed by fire",
                "answers": "What do I burn?",
                "properties": [
                    {"name": "name", "type": "STRING", "required": True},
                    {"name": "role", "type": "STRING", "enum": ["tinder", "kindling", "fuel"]},
                ],
            },
            {
                "label": "Procedure",
                "description": "A fire structure",
                "answers": "What should I build?",
                "properties": [{"name": "name", "type": "STRING", "required": True}],
            },
            {
                "label": "Technique",
                "description": "An ignition method",
                "answers": "How do I light it?",
                "properties": [{"name": "name", "type": "STRING", "required": True}],
            },
        ],
        "relationship_types": [
            {"label": "REQUIRES_MATERIAL", "description": "This procedure consumes this material"},
            {"label": "SUITED_FOR", "description": "Recommended for this condition"},
        ],
        "patterns": [
            ["Procedure", "REQUIRES_MATERIAL", "Material"],
            ["Procedure", "SUITED_FOR", "Condition"],
        ],
        "vocabularies": {
            "Material": {
                "tiers": ["exact", "fuzzy"],
                "fuzzy_threshold": 0.85,
                "entries": [
                    {"canonical": "birch bark", "surface_forms": ["birch bark", "birchbark", "birch bark shavings"]},
                    {"canonical": "dry grass", "surface_forms": ["dry grass", "dry grasses", "tinder grass"]},
                    {"canonical": "cedar bark", "surface_forms": ["cedar bark", "cedar"]},
                ],
            }
        },
        "resolution": [
            {"labels": ["Procedure", "Technique"], "strategy": "fuzzy", "threshold": 0.88},
            {"labels": ["Material"], "strategy": "vocabulary"},
        ],
    },
    "embedding": {
        "model": "all-MiniLM-L6-v2",
        "dimensions": 384,
        "targets": [
            {"name": "chunk_text", "of": "Chunk", "text": "{path}\n\n{text}"},
            {"name": "entity_card", "of": ["Material", "Procedure"], "text": "{name}."},
        ],
    },
    "drift": {
        "max_unmapped_mention_rate": 0.15,
        "max_vocabulary_miss_rate": 0.10,
        "max_degree": 200,
    },
}

SAMPLE_CHUNKS = [
    Chunk(
        source_id="src-1",
        path="Chapter 1 / Setup",
        text="Gather dry grass and birch bark for tinder.",
        char_start=0,
        char_end=51,
        blueprint_hash="sha256:test123",
        chunk_id="chunk1",
    ),
    Chunk(
        source_id="src-1",
        path="Chapter 2 / Techniques",
        text="Use a bow drill with cedar bark.",
        char_start=52,
        char_end=86,
        blueprint_hash="sha256:test123",
        chunk_id="chunk2",
    ),
]

SAMPLE_SOURCE = Source(
    source_id="src-1",
    uri="test.md",
    content_hash="sha256:abc",
    mime="text/markdown",
    blueprint_version="1.0.0",
    blueprint_hash="sha256:test123",
    added_by="tester",
    status="ready",
)

SAMPLE_EXTRACTION_RESULTS = [
    ExtractionResult(
        chunk_id="chunk1",
        source_id="src-1",
        entities=[
            ExtractedEntity(
                label="Material",
                name="dry grass",
                properties=[ExtractedProperty(name="role", value="tinder")],
                quote="dry grass",
                char_start=7,
                char_end=16,
                confidence=0.95,
            ),
            ExtractedEntity(
                label="Material",
                name="birch bark",
                properties=[ExtractedProperty(name="role", value="tinder")],
                quote="birch bark",
                char_start=22,
                char_end=32,
                confidence=0.95,
            ),
            ExtractedEntity(
                label="Procedure",
                name="teepee",
                properties=[],
                quote="teepee",
                char_start=0,
                char_end=6,
                confidence=0.9,
            ),
        ],
        relations=[
            ExtractedRelation(
                source_label="Procedure",
                source_name="teepee",
                relation="REQUIRES_MATERIAL",
                target_label="Material",
                target_name="dry grass",
                properties=[ExtractedProperty(name="stage", value="tinder")],
                quote="dry grass for tinder",
                char_start=7,
                char_end=30,
                confidence=0.85,
            ),
        ],
        unmapped_mentions=["feather stick", "char cloth"],
    ),
    ExtractionResult(
        chunk_id="chunk2",
        source_id="src-1",
        entities=[
            ExtractedEntity(
                label="Material",
                name="cedar bark",
                properties=[],
                quote="cedar bark",
                char_start=22,
                char_end=32,
                confidence=0.95,
            ),
            ExtractedEntity(
                label="Technique",
                name="bow drill",
                properties=[],
                quote="bow drill",
                char_start=7,
                char_end=16,
                confidence=0.95,
            ),
        ],
        relations=[
            ExtractedRelation(
                source_label="Technique",
                source_name="bow drill",
                relation="IGNITES",
                target_label="Material",
                target_name="cedar bark",
                properties=[],
                quote="bow drill with cedar bark",
                char_start=7,
                char_end=32,
                confidence=0.9,
            ),
        ],
        unmapped_mentions=["hand drill"],
    ),
]


# ===================================================================
# Store tests
# ===================================================================


class TestMemoryStore:
    def test_write_and_read_chunk(self) -> None:
        store = MemoryStore()
        store.write_chunk(SAMPLE_CHUNKS[0], SAMPLE_SOURCE)
        assert store.get_chunk("chunk1") is not None
        assert store.get_source("src-1") is not None
        store.close()

    def test_write_entity(self) -> None:
        store = MemoryStore()
        entity = SAMPLE_EXTRACTION_RESULTS[0].entities[0]
        store.write_entity(entity, "chunk1", "src-1", "sha256:test123")
        stored = store.get_entity("dry grass", "Material")
        assert stored is not None
        assert stored["label"] == "Material"

        # Second write to same entity should not duplicate
        store.write_entity(entity, "chunk1", "src-1", "sha256:test123")
        assert store.entity_count() == 1
        store.close()

    def test_write_relation(self) -> None:
        store = MemoryStore()
        e1 = SAMPLE_EXTRACTION_RESULTS[0].entities[0]
        e2 = SAMPLE_EXTRACTION_RESULTS[0].entities[1]
        rel = SAMPLE_EXTRACTION_RESULTS[0].relations[0]
        store.write_entity(e1, "chunk1", "src-1", "sha256:test123")
        store.write_entity(e2, "chunk1", "src-1", "sha256:test123")
        store.write_relation(rel, "chunk1", "sha256:test123")
        assert store.relation_count() == 1
        store.close()

    def test_retraction_refcounting(self) -> None:
        store = MemoryStore()
        source_a = Source(source_id="src-a", uri="a.md", content_hash="h1", mime="text", blueprint_version="1.0", blueprint_hash="bp1")
        source_b = Source(source_id="src-b", uri="b.md", content_hash="h2", mime="text", blueprint_version="1.0", blueprint_hash="bp1")

        store.write_chunk(SAMPLE_CHUNKS[0], source_a)
        store.write_chunk(SAMPLE_CHUNKS[1], source_b)

        # Share an entity between both sources
        entity = ExtractedEntity(label="Material", name="shared", properties=[], quote="", confidence=1.0)
        store.write_entity(entity, "chunk1", "src-a", "bp1")
        store.write_entity(entity, "chunk2", "src-b", "bp1")

        # Retract source A — shared entity should survive
        store.retract_source("src-a")
        assert store.get_entity("shared", "Material") is not None

        # Retract source B — entity should be orphaned
        store.retract_source("src-b")
        assert store.get_entity("shared", "Material") is None
        store.close()

    def test_store_factory(self) -> None:
        store = store_for("memory")
        assert isinstance(store, MemoryStore)
        store.close()

    def test_embedding_storage(self) -> None:
        store = MemoryStore()
        store.write_embedding("chunk1", "Chunk", [0.1, 0.2, 0.3], "bp1")
        assert "chunk1" in store.embeddings
        assert len(store.embeddings["chunk1"]) == 3
        store.close()


# ===================================================================
# Entity resolution tests
# ===================================================================


class TestEntityResolution:
    def test_exact_match_resolves(self) -> None:
        resolver = EntityResolver(SAMPLE_BLUEPRINT)
        entity = ExtractedEntity(
            label="Material",
            name="birch bark",
            properties=[],
            quote="birch bark",
            char_start=0,
            char_end=10,
            confidence=1.0,
        )
        resolved = resolver.resolve([entity])
        assert len(resolved) == 1
        assert resolved[0].canonical_name == "birch bark"
        assert resolved[0].tier == "exact"

    def test_exact_match_surface_form(self) -> None:
        resolver = EntityResolver(SAMPLE_BLUEPRINT)
        entity = ExtractedEntity(label="Material", name="birchbark", properties=[])
        resolved = resolver.resolve([entity])
        assert len(resolved) == 1
        assert resolved[0].canonical_name == "birch bark"
        assert resolved[0].tier == "exact"

    def test_new_entity_passes_through(self) -> None:
        resolver = EntityResolver(SAMPLE_BLUEPRINT)
        entity = ExtractedEntity(label="Procedure", name="log cabin", properties=[])
        resolved = resolver.resolve([entity])
        assert len(resolved) == 1
        assert resolved[0].canonical_name == "log cabin"
        assert resolved[0].tier == "new"

    def test_existing_entity_matches(self) -> None:
        resolver = EntityResolver(SAMPLE_BLUEPRINT)
        entity = ExtractedEntity(label="Material", name="cedar bark", properties=[])
        existing_id = hashlib.sha256(b"Material\x00cedar bark").hexdigest()
        resolved = resolver.resolve([entity], existing_entity_ids={existing_id})
        assert len(resolved) == 1
        assert resolved[0].tier == "existing"

    def test_entity_id_deterministic(self) -> None:
        e1 = ResolvedEntity(canonical_name="birch bark", label="Material")
        e2 = ResolvedEntity(canonical_name="birch bark", label="Material")
        assert e1.entity_id == e2.entity_id

    def test_vocabulary_from_blueprint(self) -> None:
        vocabs = Vocabulary.vocabularies_from_blueprint(SAMPLE_BLUEPRINT)
        assert "Material" in vocabs
        assert "birch bark" in vocabs["Material"].entries
        assert vocabs["Material"].fuzzy_threshold == 0.85

    def test_resolve_batch_dedup(self) -> None:
        results = [
            ExtractionResult(
                chunk_id="c1",
                source_id="src-1",
                entities=[
                    ExtractedEntity(label="Material", name="birch bark", properties=[]),
                    ExtractedEntity(label="Material", name="birchbark", properties=[]),
                ],
            ),
        ]
        resolved = resolve_batch(results, SAMPLE_BLUEPRINT)
        # Both surface forms should resolve to the same canonical entity
        assert len(resolved) == 1
        assert resolved[0].canonical_name == "birch bark"

    def test_non_vocab_label_passes_through(self) -> None:
        resolver = EntityResolver(SAMPLE_BLUEPRINT)
        entity = ExtractedEntity(label="Hazard", name="carbon monoxide", properties=[])
        resolved = resolver.resolve([entity])
        assert resolved[0].canonical_name == "carbon monoxide"
        assert resolved[0].tier == "new"


# ===================================================================
# Embedding tests
# ===================================================================


class TestEmbedding:
    def test_mock_embedder_deterministic(self) -> None:
        e = MockEmbedder(dimensions=4)
        v1 = e.embed("hello")
        v2 = e.embed("hello")
        assert v1 == v2

    def test_mock_embedder_differs_by_text(self) -> None:
        e = MockEmbedder(dimensions=4)
        v1 = e.embed("hello")
        v2 = e.embed("world")
        assert v1 != v2

    def test_mock_embedder_dimensions(self) -> None:
        e = MockEmbedder(dimensions=384)
        v = e.embed("test")
        assert len(v) == 384

    def test_mock_embedder_batch(self) -> None:
        e = MockEmbedder(dimensions=4)
        results = e.embed_batch(["a", "b"])
        assert len(results) == 2
        assert len(results[0]) == 4

    def test_embed_for_factory(self) -> None:
        e = embed_for("mock", dimensions=128)
        assert isinstance(e, MockEmbedder)
        assert e.dimensions == 128

    def test_build_embedding_targets_chunks(self) -> None:
        chunks = [
            {"chunk_id": "c1", "text": "hello", "path": "/a"},
            {"chunk_id": "c2", "text": "world", "path": "/b"},
        ]
        targets = build_embedding_targets(SAMPLE_BLUEPRINT, chunks)
        chunk_targets = [t for t in targets if t[0].startswith("chunk")]
        assert len(chunk_targets) == 2

    def test_build_embedding_targets_entities(self) -> None:
        chunks = [{"chunk_id": "c1", "text": "text", "path": "/"}]
        entities = [
            ResolvedEntity(canonical_name="birch bark", label="Material"),
            ResolvedEntity(canonical_name="teepee", label="Procedure"),
        ]
        targets = build_embedding_targets(SAMPLE_BLUEPRINT, chunks, entities)
        entity_targets = [t for t in targets if t[0].startswith("entity")]
        assert len(entity_targets) == 2


# ===================================================================
# Drift report tests
# ===================================================================


class TestDriftReport:
    def test_unmapped_mention_rate(self) -> None:
        report = build_drift_report(SAMPLE_BLUEPRINT, "src-1", SAMPLE_EXTRACTION_RESULTS)
        assert report.total_unmapped == 3  # feather stick, char cloth, hand drill
        assert report.total_entities == 5
        assert report.unmapped_mention_rate > 0

    def test_pattern_violation_detection(self) -> None:
        report = build_drift_report(SAMPLE_BLUEPRINT, "src-1", SAMPLE_EXTRACTION_RESULTS)
        # IGNITES is not in allowed patterns, so the IGNITES edge is a violation
        assert report.total_violations == 1
        assert any("IGNITES" in v.relation for v in report.pattern_violations)

    def test_no_vocab_misses_no_hubs(self) -> None:
        resolved = [
            ResolvedEntity(canonical_name="birch bark", label="Material", tier="exact"),
            ResolvedEntity(canonical_name="dry grass", label="Material", tier="exact"),
            ResolvedEntity(canonical_name="teepee", label="Procedure", tier="new"),
        ]
        report = build_drift_report(
            SAMPLE_BLUEPRINT, "src-1", SAMPLE_EXTRACTION_RESULTS, resolved
        )
        assert report.total_vocab_misses == 0
        assert len(report.hub_candidates) == 0

    def test_vocab_miss_detection(self) -> None:
        resolved = [
            ResolvedEntity(canonical_name="birch bark", label="Material", tier="embedding"),
            ResolvedEntity(canonical_name="unknown", label="Material", tier="new"),
        ]
        report = build_drift_report(
            SAMPLE_BLUEPRINT, "src-1", SAMPLE_EXTRACTION_RESULTS, resolved
        )
        assert report.total_vocab_misses == 1
        assert report.vocab_misses[0].tier == "embedding"

    def test_thresholds_exceeded(self) -> None:
        report = build_drift_report(SAMPLE_BLUEPRINT, "src-1", SAMPLE_EXTRACTION_RESULTS)
        exceeded = report.thresholds_exceeded
        # The unmapped rate (3/(3+5)=37.5%) exceeds 15%
        assert any("unmapped" in e for e in exceeded)

    def test_print_report(self) -> None:
        report = build_drift_report(SAMPLE_BLUEPRINT, "src-1", SAMPLE_EXTRACTION_RESULTS)
        text = print_report(report)
        assert "Drift Report" in text
        assert "unmapped" in text.lower()


# ===================================================================
# Pipeline integration tests
# ===================================================================


class TestPipeline:
    def test_end_to_end_resolve_embed_write(self) -> None:
        result = run_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SAMPLE_SOURCE,
            chunks=SAMPLE_CHUNKS,
            extraction_results=SAMPLE_EXTRACTION_RESULTS,
            store_backend="memory",
            embed_backend="mock",
        )
        assert result.success
        assert result.chunks_written == 2
        assert result.entities_written >= 4  # entities across both chunks
        # REQUIRES_MATERIAL is declared. IGNITES is not in the patterns, so the
        # closed ontology drops it before it reaches the store.
        assert result.relations_written == 1
        assert result.relations_dropped == 1
        assert result.drift_report.total_violations == 1  # still reported
        assert result.embeddings_written > 0
        assert result.drift_report is not None

    def test_dry_run_skips_writes(self) -> None:
        result = run_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SAMPLE_SOURCE,
            chunks=SAMPLE_CHUNKS,
            extraction_results=SAMPLE_EXTRACTION_RESULTS,
            store_backend="memory",
            embed_backend="mock",
            dry_run=True,
        )
        assert result.success
        assert result.chunks_written == 0
        assert result.entities_written == 0
        assert result.drift_report is not None

    def test_pipeline_report_format(self) -> None:
        result = run_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SAMPLE_SOURCE,
            chunks=SAMPLE_CHUNKS,
            extraction_results=SAMPLE_EXTRACTION_RESULTS,
            store_backend="memory",
            embed_backend="mock",
        )
        text = run_pipeline_report(result)
        assert "Pipeline" in text
        assert "Entities" in text
        assert "Drift Report" in text

    def test_empty_extraction(self) -> None:
        result = run_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SAMPLE_SOURCE,
            chunks=SAMPLE_CHUNKS,
            extraction_results=[],
            store_backend="memory",
            embed_backend="mock",
        )
        assert result.success
        assert result.entities_written == 0

    def test_hub_detection(self) -> None:
        # Create many results all mentioning the same entity
        results = []
        for i in range(5):
            results.append(
                ExtractionResult(
                    chunk_id=f"c{i}",
                    source_id="src-1",
                    entities=[
                        ExtractedEntity(label="Material", name="birch bark", properties=[]),
                    ],
                )
            )
        result = run_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SAMPLE_SOURCE,
            chunks=SAMPLE_CHUNKS,
            extraction_results=results,
            store_backend="memory",
            embed_backend="mock",
            hub_threshold=3,
        )
        assert result.drift_report is not None
        # birch bark appears in 5 chunks, threshold is 3, so it should be a hub
        hub_names = {h.name for h in result.drift_report.hub_candidates}
        assert "birch bark" in hub_names


# ===================================================================
# Store-for-Neo4j factory (no actual Neo4j connection)
# ===================================================================


class TestStoreFactory:
    def test_memory_store_via_factory(self) -> None:
        store = store_for("memory")
        assert isinstance(store, MemoryStore)
        store.close()

    def test_store_interface_methods(self) -> None:
        store = store_for("memory")
        # All interface methods exist without raising NotImplementedError
        store.create_indexes()
        store.set_blueprint_active("bp1")
        store.write_chunk(SAMPLE_CHUNKS[0], SAMPLE_SOURCE)
        store.close()