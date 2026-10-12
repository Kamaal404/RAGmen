"""Conflict representation — surface disagreements between sources.

From ARCHITECTURE.md:
  Two sources will disagree: different substitution ratios, contradictory
  safety advice, superseded regulations. The graph must *represent* the
  disagreement rather than let last-write-wins pick a winner. Edges carry
  ``asserted_by: [source_id]``, and Serve surfaces both with attribution.

The conflict policy is declared in the blueprint and enforced here and in
the agent layer (which handles the answer-time formatting).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from forge.extract.models import ExtractedRelation


# ---------------------------------------------------------------------------
# Conflict record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssertedRelation:
    """A relation as asserted by one source.

    Instead of last-write-wins, each source's assertion is preserved.
    """

    source_id: str
    source_uri: str
    relation: str
    source_label: str
    source_name: str
    target_label: str
    target_name: str
    properties: dict[str, Any] = field(default_factory=dict)
    quote: str = ""
    confidence: float = 1.0
    chunk_id: str = ""
    blueprint_hash: str = ""

    @property
    def assertion_key(self) -> str:
        """Deterministic key grouping assertions about the same (source, target, rel) triple."""
        raw = f"{self.source_label}\0{self.source_name}\0{self.relation}\0{self.target_label}\0{self.target_name}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ConflictGroup:
    """Group of conflicting assertions about the same relationship triple.

    Two assertions conflict when they share the same assertion_key but
    differ in properties, quote, or confidence enough to matter.
    """

    assertion_key: str
    source_label: str
    source_name: str
    relation: str
    target_label: str
    target_name: str
    assertions: tuple[AssertedRelation, ...] = ()

    @property
    def has_conflict(self) -> bool:
        """True if multiple sources assert this with measurably different claims."""
        if len(self.assertions) < 2:
            return False
        # Check for contradictory property values or significantly different quotes
        quotes = {a.quote for a in self.assertions if a.quote}
        return len(quotes) > 1

    @property
    def source_ids(self) -> list[str]:
        return [a.source_id for a in self.assertions]


# ---------------------------------------------------------------------------
# Conflict detector
# ---------------------------------------------------------------------------


def group_assertions(
    relations: list[dict[str, Any]],
    source_lookup: dict[str, str],
) -> dict[str, ConflictGroup]:
    """Group all relations by assertion_key, collecting per-source assertions.

    Args:
        relations: Raw relation dicts from MemoryStore.relations
        source_lookup: source_id -> uri mapping

    Returns:
        Dict of assertion_key -> ConflictGroup
    """
    groups: dict[str, list[AssertedRelation]] = {}

    for r in relations:
        key = _assertion_key(r["source_label"], r["source_name"],
                             r["relation"], r["target_label"], r["target_name"])
        ar = AssertedRelation(
            source_id=r.get("source_id", "unknown"),
            source_uri=source_lookup.get(r.get("source_id", ""), ""),
            relation=r["relation"],
            source_label=r["source_label"],
            source_name=r["source_name"],
            target_label=r["target_label"],
            target_name=r["target_name"],
            properties=r.get("properties", {}),
            quote=r.get("quote", ""),
            confidence=r.get("confidence", 1.0),
            chunk_id=r.get("chunk_id", ""),
            blueprint_hash=r.get("blueprint_hash", ""),
        )
        groups.setdefault(key, []).append(ar)

    result: dict[str, ConflictGroup] = {}
    for key, assertions in groups.items():
        first = assertions[0]
        result[key] = ConflictGroup(
            assertion_key=key,
            source_label=first.source_label,
            source_name=first.source_name,
            relation=first.relation,
            target_label=first.target_label,
            target_name=first.target_name,
            assertions=tuple(assertions),
        )
    return result


def find_conflicts(
    relations: list[dict[str, Any]],
    source_lookup: dict[str, str],
) -> list[ConflictGroup]:
    """Find all conflicting assertion groups."""
    return [
        g for g in group_assertions(relations, source_lookup).values()
        if g.has_conflict
    ]


def _assertion_key(
    source_label: str, source_name: str,
    relation: str, target_label: str, target_name: str,
) -> str:
    raw = f"{source_label}\0{source_name}\0{relation}\0{target_label}\0{target_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CLI-friendly output
# ---------------------------------------------------------------------------


def format_conflicts(conflicts: list[ConflictGroup]) -> str:
    if not conflicts:
        return "No conflicts detected."

    lines: list[str] = [f"Conflicts found: {len(conflicts)}"]
    for cg in conflicts:
        lines.append("")
        lines.append(f"  {cg.source_label} '{cg.source_name}' --[{cg.relation}]--> {cg.target_label} '{cg.target_name}'")
        for a in cg.assertions:
            uri = a.source_uri or a.source_id
            quote = f" \"{a.quote[:80]}\"" if a.quote else ""
            lines.append(f"    [{uri}] confidence={a.confidence}{quote}")
    return "\n".join(lines)


__all__ = [
    "AssertedRelation", "ConflictGroup",
    "find_conflicts", "group_assertions", "format_conflicts",
]