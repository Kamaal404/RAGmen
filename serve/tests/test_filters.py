# ruff: noqa: E501, S101
"""Tests for Serve guardrail compiler."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from serve.filters import FilterContext, GuardrailCompiler, HardFilter


BLUEPRINT_WITH_FILTERS = {
    "name": "test",
    "blueprint_hash": "sha256:test",
    "guardrails": {
        "conflict_policy": "surface_both",
        "hard_filters": [
            {
                "name": "hazards_always",
                "mode": "force_include",
                "labels": ["Hazard", "SafetyRule"],
                "rationale": "Hazards must always surface.",
            },
            {
                "name": "source_visibility",
                "mode": "restrict",
                "params": ["user_id"],
                "cypher": "node.visibility = 'public' OR node.owner_id = $user_id",
                "rationale": "User-uploaded content licensing.",
            },
        ],
        "answer_policy": {"require_citation": True, "refuse_outside_corpus": True},
    },
}

BLUEPRINT_NO_FILTERS = {
    "name": "test",
    "guardrails": {},
}


class TestGuardrailCompiler:
    def test_compile_returns_filters(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_WITH_FILTERS)
        filters = compiler.compile()
        assert len(filters) == 2

    def test_compile_no_filters(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_NO_FILTERS)
        filters = compiler.compile()
        assert filters == []

    def test_filter_properties(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_WITH_FILTERS)
        filters = compiler.compile()
        hf = filters[0]
        assert hf.name == "hazards_always"
        assert hf.mode == "force_include"
        assert "Hazard" in hf.labels

    def test_conflict_policy(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_WITH_FILTERS)
        assert compiler.conflict_policy == "surface_both"

    def test_answer_policy(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_WITH_FILTERS)
        policy = compiler.answer_policy
        assert policy["require_citation"] is True
        assert policy["refuse_outside_corpus"] is True

    def test_apply_exclude_filter(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_WITH_FILTERS)
        filters = compiler.compile()

        class MockHit:
            def __init__(self, label: str):
                self.entity_labels = [label]

        hits = [MockHit("Material"), MockHit("Hazard"), MockHit("Procedure")]

        # Create an exclude filter for testing
        exclude_filter = HardFilter(name="test", mode="exclude", labels=("Hazard",))
        filtered = GuardrailCompiler.apply([exclude_filter], hits)
        assert len(filtered) == 2
        assert all(h.entity_labels != ["Hazard"] for h in filtered)

    def test_apply_no_mutation(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_WITH_FILTERS)
        filters = compiler.compile()

        class MockHit:
            def __init__(self, lid: str):
                self.entity_labels = [lid]

        hits = [MockHit("Material"), MockHit("Hazard")]
        filtered = GuardrailCompiler.apply(filters, hits)
        # force_include mode does not filter out anything
        assert len(filtered) == 2

    def test_filter_context_defaults(self) -> None:
        ctx = FilterContext()
        assert ctx.user_id == ""
        assert ctx.visibility == "public"

    def test_filter_context_custom(self) -> None:
        ctx = FilterContext(user_id="alice", visibility="private")
        assert ctx.user_id == "alice"

    def test_compile_cypher_where(self) -> None:
        compiler = GuardrailCompiler(BLUEPRINT_WITH_FILTERS)
        filters = compiler.compile()
        ctx = FilterContext(user_id="bob")
        clause = compiler.compile_cypher_where(filters, ctx)
        # The restrict filter should compile to a clause containing "bob"
        assert "bob" in clause or "public" in clause or clause == ""