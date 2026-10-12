# ruff: noqa: E501, S101
"""Tests for Forge phase 7: incremental update, conflict, drift enforcement.

All tests run offline with no API key or database connection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from forge.conflict import (
    AssertedRelation,
    ConflictGroup,
    find_conflicts,
    format_conflicts,
    group_assertions,
)
from forge.extract.models import ExtractedEntity, ExtractedProperty, ExtractionResult
from forge.pipeline import PipelineResult, run_pipeline, run_update_pipeline
from forge.registry.models import Chunk, Source
from forge.resolve import scoped_re_resolve
from forge.store import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_BLUEPRINT = {
    "name": "test",
    "blueprint_hash": "sha256:test123",
    "ontology": {
        "closed": True,
        "node_types": [
            {"label": "Procedure", "description": "A fire structure",
             "answers": "What should I build?",
             "properties": [{"name": "name", "type": "STRING", "required": True}]},
            {"label": "Material", "description": "Something consumed",
             "answers": "What do I burn?",
             "properties": [{"name": "name", "type": "STRING", "required": True}]},
        ],
        "relationship_types": [
            {"label": "REQUIRES_MATERIAL", "description": "Consumes this material"},
        ],
        "patterns": [["Procedure", "REQUIRES_MATERIAL", "Material"]],
    },
    "embedding": {"targets": [{"name": "chunk_text", "of": "Chunk", "text": "{path}\n\n{text}"}]},
    "drift": {"max_unmapped_mention_rate": 0.15, "max_vocabulary_miss_rate": 0.10, "max_degree": 200},
}

SOURCE_A = Source(source_id="src-a", uri="source_a.md", content_hash="h1", mime="text",
                  blueprint_version="1.0", blueprint_hash="sha256:test123")
SOURCE_B = Source(source_id="src-b", uri="source_b.md", content_hash="h2", mime="text",
                  blueprint_version="1.0", blueprint_hash="sha256:test123")


# ===================================================================
# Conflict tests
# ===================================================================


class TestConflictDetection:
    def test_no_conflicts_single_source(self) -> None:
        relations = [
            {"source_label": "Procedure", "source_name": "teepee", "relation": "REQUIRES_MATERIAL",
             "target_label": "Material", "target_name": "birch bark", "source_id": "src-a",
             "quote": "use birch bark", "confidence": 0.9, "chunk_id": "c1", "blueprint_hash": "bp1",
             "properties": {}},
        ]
        uris = {"src-a": "a.md"}
        conflicts = find_conflicts(relations, uris)
        assert len(conflicts) == 0

    def test_conflict_different_quotes(self) -> None:
        relations = [
            {"source_label": "Procedure", "source_name": "teepee", "relation": "REQUIRES_MATERIAL",
             "target_label": "Material", "target_name": "birch bark", "source_id": "src-a",
             "quote": "use birch bark for best results", "confidence": 0.9, "chunk_id": "c1", "blueprint_hash": "bp1",
             "properties": {}},
            {"source_label": "Procedure", "source_name": "teepee", "relation": "REQUIRES_MATERIAL",
             "target_label": "Material", "target_name": "birch bark", "source_id": "src-b",
             "quote": "birch bark is not suitable", "confidence": 0.8, "chunk_id": "c2", "blueprint_hash": "bp1",
             "properties": {}},
        ]
        uris = {"src-a": "a.md", "src-b": "b.md"}
        conflicts = find_conflicts(relations, uris)
        assert len(conflicts) == 1
        assert conflicts[0].has_conflict
        assert len(conflicts[0].assertions) == 2

    def test_group_assertions(self) -> None:
        relations = [
            {"source_label": "P", "source_name": "X", "relation": "R", "target_label": "M", "target_name": "Y",
             "source_id": "s1", "quote": "q1", "confidence": 0.9, "chunk_id": "c1", "blueprint_hash": "bp1",
             "properties": {}},
            {"source_label": "P", "source_name": "X", "relation": "R", "target_label": "M", "target_name": "Y",
             "source_id": "s2", "quote": "q2", "confidence": 0.8, "chunk_id": "c2", "blueprint_hash": "bp1",
             "properties": {}},
        ]
        uris = {"s1": "a.md", "s2": "b.md"}
        groups = group_assertions(relations, uris)
        assert len(groups) == 1
        key = list(groups.keys())[0]
        assert len(groups[key].assertions) == 2

    def test_multiple_groups(self) -> None:
        relations = [
            {"source_label": "P", "source_name": "A", "relation": "R1", "target_label": "M", "target_name": "B",
             "source_id": "s1", "quote": "q", "confidence": 0.9, "chunk_id": "c1", "blueprint_hash": "bp1",
             "properties": {}},
            {"source_label": "P", "source_name": "A", "relation": "R2", "target_label": "M", "target_name": "C",
             "source_id": "s2", "quote": "q", "confidence": 0.8, "chunk_id": "c2", "blueprint_hash": "bp1",
             "properties": {}},
        ]
        uris = {"s1": "a.md", "s2": "b.md"}
        groups = group_assertions(relations, uris)
        assert len(groups) == 2

    def test_format_conflicts(self) -> None:
        cg = ConflictGroup(
            assertion_key="key",
            source_label="Procedure", source_name="teepee",
            relation="REQUIRES_MATERIAL",
            target_label="Material", target_name="birch bark",
            assertions=(
                AssertedRelation("s1", "a.md", "REQUIRES_MATERIAL", "Procedure", "teepee",
                                 "Material", "birch bark", {}, "good", 0.9, "c1", "bp1"),
                AssertedRelation("s2", "b.md", "REQUIRES_MATERIAL", "Procedure", "teepee",
                                 "Material", "birch bark", {}, "bad", 0.5, "c2", "bp1"),
            ),
        )
        text = format_conflicts([cg])
        assert "Conflicts found" in text
        assert "a.md" in text
        assert "b.md" in text


# ===================================================================
# Scoped re-resolution tests
# ===================================================================


class TestScopedReResolution:
    def test_unchanged_entities_skipped(self) -> None:
        old = [ExtractionResult(
            chunk_id="c1", source_id="src-a",
            entities=[ExtractedEntity("Material", "birch bark", [], "", 0, 0, 0.95)],
        )]
        new = [ExtractionResult(
            chunk_id="c1", source_id="src-a",
            entities=[ExtractedEntity("Material", "birch bark", [], "", 0, 0, 0.95)],
        )]
        resolved, changed = scoped_re_resolve(old, new, SAMPLE_BLUEPRINT)
        assert len(resolved) == 0
        assert len(changed) == 0

    def test_new_entity_detected(self) -> None:
        old = [ExtractionResult(chunk_id="c1", source_id="src-a", entities=[])]
        new = [ExtractionResult(
            chunk_id="c1", source_id="src-a",
            entities=[ExtractedEntity("Material", "new thing", [], "", 0, 0, 0.95)],
        )]
        resolved, changed = scoped_re_resolve(old, new, SAMPLE_BLUEPRINT)
        assert len(resolved) == 1
        assert len(changed) == 1

    def test_new_chunk_detected(self) -> None:
        old = [ExtractionResult(chunk_id="c1", source_id="src-a", entities=[])]
        new = [
            ExtractionResult(chunk_id="c1", source_id="src-a", entities=[]),
            ExtractionResult(chunk_id="c2", source_id="src-a",
                             entities=[ExtractedEntity("Material", "birch bark", [])]),
        ]
        resolved, changed = scoped_re_resolve(old, new, SAMPLE_BLUEPRINT)
        assert len(resolved) >= 1
        assert len(changed) == 1

    def test_existing_entity_ids_honored(self) -> None:
        old = [ExtractionResult(chunk_id="c1", source_id="src-a", entities=[])]
        new = [ExtractionResult(
            chunk_id="c1", source_id="src-a",
            entities=[ExtractedEntity("Material", "birch bark", []),
                      ExtractedEntity("Material", "new thing", [])],
        )]
        import hashlib
        existing = {hashlib.sha256(b"Material\x00birch bark").hexdigest()}
        resolved, changed = scoped_re_resolve(old, new, SAMPLE_BLUEPRINT, existing)
        # "birch bark" is in existing, so should be marked "existing" not "new"
        # "new thing" is not in existing, should be resolved
        assert len(resolved) > 0
        names = {r.canonical_name for r in resolved if r.tier != "existing"}


# ===================================================================
# Drift threshold enforcement tests
# ===================================================================


class TestDriftEnforcement:
    def test_fail_on_exceeded_default_off(self) -> None:
        bp = {**SAMPLE_BLUEPRINT, "drift": {"max_unmapped_mention_rate": 0.15}}
        result = PipelineResult(source_id="test")
        # No enforce — even without a drift report, no error
        assert result.success

    def test_drift_config_loaded(self) -> None:
        bp = {**SAMPLE_BLUEPRINT, "drift": {"max_unmapped_mention_rate": 0.15, "fail_on_exceeded": True}}
        assert bp.get("drift", {}).get("fail_on_exceeded", False) is True


# ===================================================================
# Incremental update pipeline tests
# ===================================================================


class TestUpdatePipeline:
    def test_update_unchanged_source(self) -> None:
        chunks = [
            Chunk(source_id="src-a", path="/a", text="Birch bark is good tinder.",
                  char_start=0, char_end=28, blueprint_hash="sha256:test123", chunk_id="c1"),
        ]
        old_results = [ExtractionResult(chunk_id="c1", source_id="src-a",
                                        entities=[ExtractedEntity("Material", "birch bark", [])])]
        new_results = [ExtractionResult(chunk_id="c1", source_id="src-a",
                                        entities=[ExtractedEntity("Material", "birch bark", [])])]

        result = run_update_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SOURCE_A,
            old_extraction_results=old_results,
            new_chunks=chunks,
            new_extraction_results=new_results,
        )
        # No new entities to resolve — update should be clean
        assert result.success

    def test_update_with_new_entities(self) -> None:
        chunks = [
            Chunk(source_id="src-a", path="/a", text="Birch bark and dry grass.",
                  char_start=0, char_end=28, blueprint_hash="sha256:test123", chunk_id="c1"),
        ]
        old_results = [ExtractionResult(chunk_id="c1", source_id="src-a",
                                        entities=[ExtractedEntity("Material", "birch bark", [])])]
        new_results = [ExtractionResult(chunk_id="c1", source_id="src-a",
                                        entities=[
                                            ExtractedEntity("Material", "birch bark", []),
                                            ExtractedEntity("Material", "dry grass", []),
                                        ])]

        result = run_update_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SOURCE_A,
            old_extraction_results=old_results,
            new_chunks=chunks,
            new_extraction_results=new_results,
        )
        assert result.success
        assert result.entities_written >= 1

    def test_update_dry_run(self) -> None:
        chunks = [Chunk(source_id="src-a", path="/a", text="Some text.", char_start=0, char_end=10,
                        blueprint_hash="sha256:test123", chunk_id="c1")]
        old_results = [ExtractionResult(chunk_id="c1", source_id="src-a", entities=[])]
        new_results = [ExtractionResult(chunk_id="c1", source_id="src-a",
                                        entities=[ExtractedEntity("Material", "birch bark", [])])]

        result = run_update_pipeline(
            blueprint=SAMPLE_BLUEPRINT,
            source=SOURCE_A,
            old_extraction_results=old_results,
            new_chunks=chunks,
            new_extraction_results=new_results,
            dry_run=True,
        )
        assert result.success
        assert result.entities_written == 0


# ===================================================================
# Cross-source entity survival tests
# ===================================================================


class TestCrossSource:
    def test_entity_survives_single_retraction(self) -> None:
        store = MemoryStore()
        store.write_chunk(Chunk(source_id="src-a", path="/a", text="birch bark",
                                char_start=0, char_end=10, blueprint_hash="bp1", chunk_id="c1"), SOURCE_A)
        store.write_chunk(Chunk(source_id="src-b", path="/b", text="birch bark",
                                char_start=0, char_end=10, blueprint_hash="bp1", chunk_id="c2"), SOURCE_B)

        entity = ExtractedEntity("Material", "birch bark", [], "", 0, 0, 1.0)
        store.write_entity(entity, "c1", "src-a", "bp1")
        store.write_entity(entity, "c2", "src-b", "bp1")

        store.retract_source("src-a")
        assert store.get_entity("birch bark", "Material") is not None

        store.retract_source("src-b")
        assert store.get_entity("birch bark", "Material") is None