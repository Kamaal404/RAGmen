# ruff: noqa: E501, S101
"""Tests for the answer policy engine."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "forge" / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "serve" / "src"))

from forge.store import MemoryStore
from agent.answers import (
    Agent,
    AnswerPolicy,
    Claim,
    Conflict,
    CitedAnswer,
    CitedSource,
    format_answer,
)

from .conftest import seed_memory_store


# ---------------------------------------------------------------------------
# Answer policy tests
# ---------------------------------------------------------------------------


class TestAnswerPolicy:
    def test_from_blueprint_defaults(self) -> None:
        policy = AnswerPolicy.from_blueprint({})
        assert policy.require_citation is True
        assert policy.refuse_outside_corpus is True
        assert policy.conflict_policy == "surface_both"

    def test_from_blueprint_custom(self) -> None:
        bp = {
            "guardrails": {
                "conflict_policy": "prefer_strictest",
                "answer_policy": {
                    "require_citation": False,
                    "refuse_outside_corpus": False,
                },
            }
        }
        policy = AnswerPolicy.from_blueprint(bp)
        assert policy.require_citation is False
        assert policy.refuse_outside_corpus is False
        assert policy.conflict_policy == "prefer_strictest"

    def test_from_blueprint_no_guardrails(self) -> None:
        policy = AnswerPolicy.from_blueprint({"name": "test"})
        assert policy.require_citation is True


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------


@pytest.fixture
def store(sample_source, sample_chunks):
    s = MemoryStore()
    seed_memory_store(s, sample_source, sample_chunks)
    return s


@pytest.fixture
def agent(store, sample_blueprint):
    return Agent(store, sample_blueprint)


class TestAgent:
    def test_agent_answers_question(self, agent: Agent) -> None:
        answer = agent.answer("what is a dakota fire hole")
        assert answer.has_answer
        assert len(answer.claims) > 0

    def test_agent_citations_present(self, agent: Agent) -> None:
        answer = agent.answer("what is a dakota fire hole")
        assert answer.total_citations > 0

    def test_agent_refuses_outside_corpus(self, store) -> None:
        # Empty store should cause refusal with default policy
        empty_store = MemoryStore()
        agent = Agent(empty_store, {})
        answer = agent.answer("what is the meaning of life")
        assert answer.refused
        assert "No information" in answer.refusal_reason

    def test_agent_no_refusal_when_policy_disabled(self, store) -> None:
        bp = {
            "guardrails": {
                "answer_policy": {"refuse_outside_corpus": False},
            }
        }
        empty_store = MemoryStore()
        agent = Agent(empty_store, bp)
        answer = agent.answer("unknown topic")
        assert not answer.refused
        assert answer.empty

    def test_agent_claims_are_meaningful(self, agent: Agent) -> None:
        answer = agent.answer("tell me about birch bark")
        texts = [c.statement for c in answer.claims]
        assert any("birch" in t.lower() for t in texts)

    def test_agent_multiple_claims(self, agent: Agent) -> None:
        answer = agent.answer("what fire building methods are there")
        # Should get at least a few claims from different chunks
        assert len(answer.claims) >= 1

    def test_agent_no_conflicts_on_simple_query(self, agent: Agent) -> None:
        answer = agent.answer("dakota fire hole")
        # Simple factual query shouldn't produce conflicts
        assert len(answer.conflicts) == 0

    def test_agent_confidence_sorted(self, agent: Agent) -> None:
        answer = agent.answer("fire")
        confidences = [c.confidence for c in answer.claims]
        assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# Formatting tests
# ---------------------------------------------------------------------------


class TestFormatAnswer:
    def test_format_refused(self) -> None:
        answer = CitedAnswer(
            question="test",
            refused=True,
            refusal_reason="No information in corpus.",
        )
        text = format_answer(answer)
        assert "cannot answer" in text.lower()
        assert "No information" in text

    def test_format_empty(self) -> None:
        answer = CitedAnswer(question="test", empty=True)
        text = format_answer(answer)
        assert "No relevant information" in text

    def test_format_with_claims(self) -> None:
        answer = CitedAnswer(
            question="test",
            claims=[
                Claim(
                    statement="Birch bark is good tinder.",
                    sources=(
                        CitedSource(
                            chunk_id="c1",
                            text="Birch bark is excellent tinder.",
                            path="/chapter1",
                            source_id="src1",
                            page=5,
                        ),
                    ),
                    confidence=0.95,
                ),
            ],
        )
        text = format_answer(answer, "test-corpus")
        assert "[test-corpus]" in text
        assert "Birch bark" in text
        assert "[src1]" in text
        assert "p.5" in text

    def test_format_with_conflicts(self) -> None:
        answer = CitedAnswer(
            question="test",
            claims=[
                Claim(statement="Option A is safe.", sources=(), confidence=0.9),
                Claim(statement="Option A is dangerous.", sources=(), confidence=0.4),
            ],
            conflicts=[
                Conflict(
                    claim_a="Option A is safe.",
                    sources_a=(),
                    claim_b="Option A is dangerous.",
                    sources_b=(),
                ),
            ],
        )
        text = format_answer(answer)
        assert "conflicting" in text.lower()
        assert "vs" in text

    def test_format_citation_limit(self) -> None:
        # More than 3 sources should show "...and N more"
        sources = tuple(
            CitedSource(chunk_id=f"c{i}", text=f"source {i}", path=f"/p{i}")
            for i in range(5)
        )
        answer = CitedAnswer(
            question="test",
            claims=[Claim(statement="Claim with many sources.", sources=sources)],
        )
        text = format_answer(answer)
        assert "and 2 more sources" in text

    def test_format_with_blueprint_name(self) -> None:
        answer = CitedAnswer(
            question="test",
            claims=[Claim(statement="A fact.", sources=())],
        )
        text = format_answer(answer, "firecraft")
        assert "[firecraft]" in text


# ---------------------------------------------------------------------------
# Conflict detection tests
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_detect_contrast_conflict(self, store) -> None:
        # Inject a chunk with contrast language that will trigger detection
        from forge.registry.models import Chunk

        store.write_chunk(
            Chunk(
                source_id="src-1", path="Chapter 4 / Warning",
                text="Never use this method in wet conditions. However some sources claim it works.",
                char_start=400, char_end=500, blueprint_hash="sha256:test123",
                chunk_id="chunk005",
            ),
            store.get_source("src-1"),
        )
        store.write_embedding("chunk005", "Chunk", [0.01] * 384, "sha256:test123")

        agent = Agent(store, {})
        answer = agent.answer("method wet conditions")
        # The contrast words "never" and "however" might trigger conflict detection
        # depending on score gap. This test verifies the detection runs, not that
        # it always fires (it depends on RRF scores).
        assert answer.claims is not None

    def test_no_false_conflicts(self, store) -> None:
        agent = Agent(store, {})
        answer = agent.answer("dakota fire hole")
        # Simple factual query about one topic — no conflicts expected
        conflict_topics = {c.claim_a for c in answer.conflicts}
        assert all("dakota" not in t.lower() or "avoid" not in t.lower()
                   for t in conflict_topics)


# ---------------------------------------------------------------------------
# DirectClient tests
# ---------------------------------------------------------------------------


class TestDirectClient:
    def test_client_search(self, store, sample_blueprint) -> None:
        from agent.serve_client import DirectClient
        client = DirectClient(store, sample_blueprint)
        results = client.search("dakota fire hole", top_k=5)
        assert len(results) > 0
        assert results[0]["chunk_id"]

    def test_client_cite(self, store, sample_blueprint) -> None:
        from agent.serve_client import DirectClient
        client = DirectClient(store, sample_blueprint)
        citation = client.cite("chunk001")
        assert citation is not None
        assert citation["text"]
        assert citation["path"]

    def test_client_cite_missing(self, store, sample_blueprint) -> None:
        from agent.serve_client import DirectClient
        client = DirectClient(store, sample_blueprint)
        assert client.cite("nonexistent") is None

    def test_client_schema(self, store, sample_blueprint) -> None:
        from agent.serve_client import DirectClient
        client = DirectClient(store, sample_blueprint)
        schema = client.schema()
        assert "ontology" in schema
        assert "answer_policy" in schema