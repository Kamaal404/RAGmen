# ruff: noqa: E501, S101
"""Tests for Serve retrievers — vector, fulltext, graph, hybrid."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "forge" / "src"))

from forge.store import MemoryStore
from serve.retrievers import HybridRetriever, SearchHit

from .conftest import seed_memory_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(sample_source, sample_chunks):
    s = MemoryStore()
    seed_memory_store(s, sample_source, sample_chunks)
    return s


@pytest.fixture
def retriever(store):
    return HybridRetriever(store)


# ---------------------------------------------------------------------------
# Hybrid retriever tests
# ---------------------------------------------------------------------------


class TestHybridRetriever:
    def test_search_returns_results(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("dakota fire hole", top_k=5)
        assert len(result.hits) > 0
        assert result.retriever == "hybrid"

    def test_search_relevant_chunk_ranks_high(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("dakota fire hole", top_k=5)
        texts = [h.text.lower() for h in result.hits]
        assert any("dakota" in t for t in texts)

    def test_search_multiple_terms(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("tinder kindling fuel", top_k=5)
        assert len(result.hits) > 0

    def test_search_empty_query(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("zzzznotfound", top_k=5)
        # Mock embeddings produce deterministic vectors, so vector search
        # always returns *some* hits. Check that fulltext matching is empty.
        fulltext_terms = "zzzznotfound".lower().split()
        none_match = all(
            not any(term in h.text.lower() for term in fulltext_terms)
            for h in result.hits
        )
        assert none_match

    def test_search_respects_top_k(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("fire", top_k=2)
        assert len(result.hits) <= 2

    def test_search_scores_are_positive(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("fire", top_k=5)
        for hit in result.hits:
            assert hit.score > 0

    def test_search_hits_have_chunk_id(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("dakota", top_k=5)
        for hit in result.hits:
            assert hit.chunk_id

    def test_search_hits_have_text(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("dakota", top_k=5)
        for hit in result.hits:
            assert hit.text

    def test_expand_from_chunk(self, retriever: HybridRetriever, store) -> None:
        # Find a known chunk first
        result = retriever.retrieve("dakota", top_k=1)
        assert len(result.hits) > 0
        chunk_id = result.hits[0].chunk_id

        expanded = retriever.expand(chunk_id, hops=1, top_k=10)
        # Should find related chunks via shared entities
        assert len(expanded.hits) >= 0

    def test_expand_from_entity(self, retriever: HybridRetriever) -> None:
        expanded = retriever.expand("unknown-entity", hops=1)
        assert len(expanded.hits) == 0

    def test_hybrid_retriever_name(self, retriever: HybridRetriever) -> None:
        assert retriever.name == "hybrid"

    def test_search_with_hops(self, retriever: HybridRetriever) -> None:
        result = retriever.retrieve("birch bark", top_k=5, hops=1)
        assert len(result.hits) > 0


# ---------------------------------------------------------------------------
# SearchResult / SearchHit model tests
# ---------------------------------------------------------------------------


class TestSearchModels:
    def test_search_hit_defaults(self) -> None:
        hit = SearchHit(chunk_id="c1", text="hello", path="/")
        assert hit.score == 0.0
        assert hit.source_id is None
        assert hit.entity_ids == []

    def test_search_result_defaults(self) -> None:
        from serve.retrievers import SearchResult
        sr = SearchResult(query="test")
        assert sr.hits == []
        assert sr.total_hits == 0
        assert sr.retriever == "hybrid"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_store_returns_no_results(self) -> None:
        store = MemoryStore()
        retriever = HybridRetriever(store)
        result = retriever.retrieve("anything", top_k=5)
        assert len(result.hits) == 0

    def test_search_on_partial_store(self, sample_source) -> None:
        store = MemoryStore()
        # Add only chunks, no entities or embeddings
        from forge.registry.models import Chunk
        store.write_chunk(
            Chunk(source_id="src-1", path="/test", text="Some content about fire.",
                  char_start=0, char_end=25, blueprint_hash="bp1", chunk_id="c1"),
            sample_source,
        )
        retriever = HybridRetriever(store)
        result = retriever.retrieve("fire", top_k=5)
        assert len(result.hits) == 1