"""Shared fixtures for agent tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "forge" / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "serve" / "src"))

from forge.extract.models import ExtractedEntity, ExtractedProperty, ExtractedRelation
from forge.registry.models import Chunk, Source


@pytest.fixture(scope="session")
def sample_source() -> Source:
    return Source(
        source_id="src-1",
        uri="test.md",
        content_hash="sha256:abc",
        mime="text/markdown",
        blueprint_version="1.0.0",
        blueprint_hash="sha256:test123",
        added_by="tester",
        status="ready",
    )


@pytest.fixture(scope="session")
def sample_chunks() -> list[Chunk]:
    return [
        Chunk(source_id="src-1", path="Chapter 1 / Tinder",
              text="Gather dry grass and birch bark for tinder. Feather sticks work well too.",
              char_start=0, char_end=80, blueprint_hash="sha256:test123",
              chunk_id="chunk001", page=1),
        Chunk(source_id="src-1", path="Chapter 2 / Dakota Fire Hole",
              text="The Dakota fire hole is a wind-resistant fire structure. Dig a hole first.",
              char_start=81, char_end=160, blueprint_hash="sha256:test123",
              chunk_id="chunk002", page=5),
        Chunk(source_id="src-1", path="Chapter 2 / Log Cabin",
              text="The log cabin lay is built by stacking kindling in a square pattern.",
              char_start=161, char_end=240, blueprint_hash="sha256:test123",
              chunk_id="chunk003", page=7),
        Chunk(source_id="src-1", path="Chapter 3 / Safety",
              text="Carbon monoxide is a hazard in snow shelters. Ensure ventilation.",
              char_start=241, char_end=320, blueprint_hash="sha256:test123",
              chunk_id="chunk004", page=12),
    ]


@pytest.fixture(scope="session")
def sample_blueprint() -> dict:
    return {
        "name": "test-corpus",
        "ontology": {
            "closed": True,
            "node_types": [
                {"label": "Procedure", "description": "A fire structure",
                 "answers": "What should I build?",
                 "properties": [{"name": "name", "type": "STRING", "required": True}]},
                {"label": "Material", "description": "Something consumed",
                 "answers": "What do I burn?",
                 "properties": [{"name": "name", "type": "STRING", "required": True},
                                {"name": "role", "type": "STRING"}]},
            ],
            "relationship_types": [
                {"label": "REQUIRES_MATERIAL", "description": "Consumes this material"},
            ],
            "patterns": [["Procedure", "REQUIRES_MATERIAL", "Material"]],
        },
        "guardrails": {
            "conflict_policy": "surface_both",
            "answer_policy": {
                "require_citation": True,
                "refuse_outside_corpus": True,
            },
        },
    }


def seed_memory_store(store, source: Source, chunks: list[Chunk]) -> None:
    from forge.extract.models import ExtractedEntity, ExtractedProperty

    for chunk in chunks:
        store.write_chunk(chunk, source)

    entities = [
        ExtractedEntity("Material", "birch bark", [ExtractedProperty("role", "tinder")], "birch bark", 20, 30, 0.95),
        ExtractedEntity("Material", "dry grass", [ExtractedProperty("role", "tinder")], "dry grass", 7, 16, 0.95),
        ExtractedEntity("Procedure", "dakota fire hole", [], "Dakota fire hole", 81, 98, 0.95),
        ExtractedEntity("Procedure", "log cabin", [], "log cabin", 161, 170, 0.95),
        ExtractedEntity("Technique", "bow drill", [], "bow drill", 0, 8, 0.9),
        ExtractedEntity("Hazard", "carbon monoxide", [], "Carbon monoxide", 241, 257, 0.95),
    ]
    for e in entities:
        store.write_entity(e, chunks[0].chunk_id, source.source_id, source.blueprint_hash)
        if "dakota" in e.name:
            store.write_entity(e, chunks[1].chunk_id, source.source_id, source.blueprint_hash)
        elif "log" in e.name:
            store.write_entity(e, chunks[2].chunk_id, source.source_id, source.blueprint_hash)
        elif "carbon" in e.name:
            store.write_entity(e, chunks[3].chunk_id, source.source_id, source.blueprint_hash)

    store.write_embedding(chunks[0].chunk_id, "Chunk", [0.1] * 384, source.blueprint_hash)
    store.write_embedding(chunks[1].chunk_id, "Chunk", [0.2] * 384, source.blueprint_hash)
    store.write_embedding(chunks[2].chunk_id, "Chunk", [0.15] * 384, source.blueprint_hash)
    store.write_embedding(chunks[3].chunk_id, "Chunk", [0.05] * 384, source.blueprint_hash)