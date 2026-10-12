"""Answer policy engine — format retrieval results into cited answers.

This is the policy layer. It reads the blueprint's guardrails and produces
answers that respect them. No LLM, no prompt — just deterministic formatting
of what the retrievers found.

The agent is corpus-agnostic. It reads whatever the blueprint says and
formats whatever the retrievers return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Answer policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnswerPolicy:
    """Declared answer policy from the blueprint's guardrails section.

    All defaults match the blueprint schema defaults.
    """

    require_citation: bool = True
    refuse_outside_corpus: bool = True
    conflict_policy: str = "surface_both"  # surface_both | prefer_strictest | prefer_newest | prefer_source_rank

    @classmethod
    def from_blueprint(cls, blueprint: dict) -> AnswerPolicy:
        guardrails = blueprint.get("guardrails", {})
        ap = guardrails.get("answer_policy", {})
        return cls(
            require_citation=ap.get("require_citation", True),
            refuse_outside_corpus=ap.get("refuse_outside_corpus", True),
            conflict_policy=guardrails.get("conflict_policy", "surface_both"),
        )


# ---------------------------------------------------------------------------
# Cited answer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CitedSource:
    """A single cited source for a claim."""

    chunk_id: str
    text: str
    path: str
    source_id: str | None = None
    page: int | None = None
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class Claim:
    """A single claim with its supporting citations."""

    statement: str
    sources: tuple[CitedSource, ...] = ()
    confidence: float = 1.0  # 0.0 = weak support, 1.0 = strong


@dataclass(frozen=True, slots=True)
class Conflict:
    """Two claims that conflict, with their respective sources."""

    claim_a: str
    sources_a: tuple[CitedSource, ...]
    claim_b: str
    sources_b: tuple[CitedSource, ...]


@dataclass
class CitedAnswer:
    """A complete, policy-compliant answer to one question."""

    question: str
    claims: list[Claim] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str = ""
    empty: bool = False

    @property
    def total_citations(self) -> int:
        return sum(len(c.sources) for c in self.claims)

    @property
    def has_answer(self) -> bool:
        return not self.refused and not self.empty


def format_answer(answer: CitedAnswer, blueprint_name: str = "") -> str:
    """Format a CitedAnswer as human-readable text.

    Handles all states: refused, empty, normal with claims, with conflicts.
    """
    lines: list[str] = []

    if blueprint_name:
        lines.append(f"[{blueprint_name}]")

    if answer.refused:
        lines.append(f"I cannot answer this.\n{answer.refusal_reason}")
        return "\n".join(lines)

    if answer.empty:
        lines.append("No relevant information found in the corpus.")
        return "\n".join(lines)

    # Claims
    for i, claim in enumerate(answer.claims):
        lines.append("")
        lines.append(claim.statement)
        for src in claim.sources[:3]:  # max 3 citations per claim
            citation = _format_source(src)
            if citation:
                lines.append(f"  {citation}")
        if len(claim.sources) > 3:
            lines.append(f"  ... and {len(claim.sources) - 3} more sources")

    # Conflicts
    if answer.conflicts:
        lines.append("")
        lines.append("---")
        lines.append("Note: the corpus contains conflicting information on this topic:")
        for c in answer.conflicts:
            lines.append("")
            lines.append(f"  {c.claim_a}")
            for src in c.sources_a[:2]:
                lines.append(f"    {_format_source(src)}")
            lines.append(f"  vs {c.claim_b}")
            for src in c.sources_b[:2]:
                lines.append(f"    {_format_source(src)}")
            if c.sources_a or c.sources_b:
                lines.append("  (conflict surfaced per blueprint policy)")

    return "\n".join(lines)


def _format_source(src: CitedSource) -> str:
    """Format one cited source as a one-liner."""
    parts = []
    if src.source_id:
        parts.append(f"[{src.source_id}]")
    if src.page:
        parts.append(f"p.{src.page}")
    if src.path:
        parts.append(src.path)
    ref = " ".join(parts)
    preview = src.text[:120].replace("\n", " ")
    return f"  \u2014 {ref}: \"{preview}\""


# ---------------------------------------------------------------------------
# Agent — orchestrates retrieval + policy enforcement
# ---------------------------------------------------------------------------


class Agent:
    """Agent that answers questions by calling Serve retrievers and applying policy.

    Domain-agnostic. The blueprint's ontology and guardrails determine behaviour.

    Usage::

        agent = Agent(store, blueprint)
        answer = agent.answer("what is a dakota fire hole")
        print(format_answer(answer))
    """

    def __init__(self, store: Any, blueprint: dict | None = None) -> None:  # noqa: ANN401
        from serve.retrievers import HybridRetriever

        self._store = store
        self._blueprint = blueprint or {}
        self._retriever = HybridRetriever(store)
        self._policy = AnswerPolicy.from_blueprint(self._blueprint)

    def answer(
        self,
        question: str,
        top_k: int = 10,
        hops: int = 1,
    ) -> CitedAnswer:
        """Answer a question using the corpus and answer policy.

        Flow:
        1. Retrieve relevant chunks via hybrid search
        2. If nothing found and refuse_outside_corpus is set, refuse
        3. Build claims from retrieved chunks
        4. Detect conflicts (same topic, contradictory claims)
        5. Format with citations

        Args:
            question: Natural language question.
            top_k: Number of chunks to retrieve.
            hops: Graph traversal depth.

        Returns:
            CitedAnswer with claims, citations, and any conflicts.
        """
        result = self._retriever.retrieve(question, top_k=top_k, hops=hops)

        if not result.hits:
            if self._policy.refuse_outside_corpus:
                return CitedAnswer(
                    question=question,
                    refused=True,
                    refusal_reason=(
                        "No information found in the corpus. "
                        "I can only answer from the loaded documents."
                    ),
                )
            return CitedAnswer(question=question, empty=True)

        # Build claims from search hits
        claims = self._build_claims(result.hits, question)

        # Detect conflicts
        conflicts = self._detect_conflicts(claims)

        # If conflicts exist and policy says prefer_strictest, resolve
        if conflicts and self._policy.conflict_policy == "prefer_strictest":
            claims = self._resolve_strictest(claims, conflicts)

        return CitedAnswer(
            question=question,
            claims=claims,
            conflicts=conflicts if self._policy.conflict_policy == "surface_both" else [],
        )

    def _build_claims(
        self,
        hits: list,
        question: str,
    ) -> list[Claim]:
        """Build claims from search hits.

        Each hit becomes a claim backed by its own text as a citation.
        Related hits (same path prefix) are grouped into one claim.
        """
        # Group hits by top-level path prefix
        groups: dict[str, list] = {}
        for hit in hits:
            prefix = hit.path.split("/")[0] if hit.path else "general"
            groups.setdefault(prefix, []).append(hit)

        claims: list[Claim] = []
        for prefix, group in groups.items():
            sources = tuple(
                CitedSource(
                    chunk_id=h.chunk_id,
                    text=h.text,
                    path=h.path,
                    source_id=h.source_id,
                    page=h.page,
                    score=h.score,
                )
                for h in group
            )

            # Build a statement from the top hit's text
            top = group[0]
            text = top.text[:200].strip()

            # If the group has entity names, mention them
            entity_hint = ""
            if hasattr(top, "entity_names") and top.entity_names:
                names = top.entity_names[:3]
                entity_hint = f" (related: {', '.join(names)})"

            statement = f"According to the corpus{entity_hint}: {text}"

            # Confidence = average score
            confidence = sum(h.score for h in group) / max(1, len(group))

            claims.append(Claim(statement=statement, sources=sources, confidence=confidence))

        # Sort by confidence descending
        claims.sort(key=lambda c: c.confidence, reverse=True)
        return claims

    def _detect_conflicts(self, claims: list[Claim]) -> list[Conflict]:
        """Detect conflicting claims within the same answer.

        Simple heuristic: if two claims have scores at opposite ends and
        their text snippets use contrasting language (but, however, avoid,
        never, don't), flag as a potential conflict.

        In production this would use entity overlap + relation polarity.
        """
        conflicts: list[Conflict] = []
        contrast_words = {
            "but", "however", "avoid", "never", "don't", "do not",
            "instead", "rather", "unlike", "contrary", "caution",
            "warning", "dangerous", "unsafe", "bad", "poor",
        }

        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                text_i = claims[i].statement.lower()
                text_j = claims[j].statement.lower()

                # Check for contrast words in either text
                has_contrast = bool(
                    (contrast_words & set(text_i.split()))
                    or (contrast_words & set(text_j.split()))
                )

                # Check for significant score gap (suggests disagreement
                # in retrieval relevance)
                score_gap = abs(claims[i].confidence - claims[j].confidence) > 0.3

                if has_contrast and score_gap:
                    lower, higher = (claims[j], claims[i]) if claims[i].confidence < claims[j].confidence else (claims[i], claims[j])
                    conflicts.append(
                        Conflict(
                            claim_a=higher.statement,
                            sources_a=higher.sources,
                            claim_b=lower.statement,
                            sources_b=lower.sources,
                        )
                    )

        return conflicts

    def _resolve_strictest(
        self,
        claims: list[Claim],
        conflicts: list[Conflict],
    ) -> list[Claim]:
        """For prefer_strictest policy, keep the stricter claim.

        Stricter = higher confidence score, which correlates with
        more specific/relevant evidence.
        """
        # Filter out the lower-confidence claim in each conflict
        remove_ids: set[int] = set()
        for c in conflicts:
            # Find which claim indices match
            for i, claim in enumerate(claims):
                if claim.statement == c.claim_b and i not in remove_ids:
                    remove_ids.add(i)
                    break

        return [c for i, c in enumerate(claims) if i not in remove_ids]

    @property
    def policy(self) -> AnswerPolicy:
        return self._policy


__all__ = [
    "Agent",
    "AnswerPolicy",
    "CitedAnswer",
    "Claim",
    "CitedSource",
    "Conflict",
    "format_answer",
]