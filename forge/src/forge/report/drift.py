# ruff: noqa: E501
"""Drift report — quantitative feedback from Forge to Blueprint.

Metrics tracked:
  - unmapped_mention_rate: spans the extractor flagged as meaningful but that
    fit no declared type. Rising = ontology going stale.
  - pattern_violation_rate: relationships the model tried to make that the
    blueprint forbids. Sometimes the model is right.
  - vocabulary_miss_rate: surface forms that hit the expensive fallback tiers.
    These are the next vocabulary entries.
  - hub candidates: entities with degree above threshold. Almost always a
    resolution failure.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from forge.extract.models import ExtractionResult, ExtractedRelation
from forge.resolve import ResolvedEntity


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnmappedMention:
    chunk_id: str
    text: str


@dataclass(frozen=True, slots=True)
class PatternViolation:
    chunk_id: str
    relation: str
    source_label: str
    target_label: str
    reason: str


@dataclass(frozen=True, slots=True)
class VocabMiss:
    entity_id: str
    label: str
    surface_form: str
    tier: str  # "embedding" or "llm"


@dataclass(frozen=True, slots=True)
class HubCandidate:
    entity_id: str
    label: str
    name: str
    degree: int
    threshold: int


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Aggregate drift metrics for one extraction run."""

    blueprint_name: str
    blueprint_hash: str
    source_id: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Metrics
    unmapped_mention_rate: float = 0.0
    unmapped_mentions: tuple[UnmappedMention, ...] = ()
    pattern_violation_rate: float = 0.0
    pattern_violations: tuple[PatternViolation, ...] = ()
    vocab_miss_rate: float = 0.0
    vocab_misses: tuple[VocabMiss, ...] = ()
    hub_candidates: tuple[HubCandidate, ...] = ()

    # Counts
    total_chunks: int = 0
    total_entities: int = 0
    total_relations: int = 0
    total_unmapped: int = 0
    total_violations: int = 0
    total_vocab_misses: int = 0

    # Thresholds (from blueprint.drift)
    max_unmapped_mention_rate: float = 0.15
    max_vocab_miss_rate: float = 0.10
    max_degree: int = 200

    @property
    def thresholds_exceeded(self) -> list[str]:
        exceeded: list[str] = []
        if self.unmapped_mention_rate > self.max_unmapped_mention_rate:
            exceeded.append(
                f"unmapped_mention_rate ({self.unmapped_mention_rate:.1%} > "
                f"{self.max_unmapped_mention_rate:.1%})"
            )
        if self.vocab_miss_rate > self.max_vocab_miss_rate:
            exceeded.append(
                f"vocab_miss_rate ({self.vocab_miss_rate:.1%} > "
                f"{self.max_vocab_miss_rate:.1%})"
            )
        if self.hub_candidates:
            n_over = sum(
                1 for h in self.hub_candidates if h.degree >= self.max_degree
            )
            if n_over > 0:
                exceeded.append(
                    f"{n_over} hub(s) with degree >= {self.max_degree}"
                )
        return exceeded


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _build_patterns_set(ontology: dict) -> set[tuple[str, str, str]]:
    """Return set of (source_label, relation, target_label) allowed patterns."""
    patterns = ontology.get("patterns", [])
    return {tuple(p) for p in patterns}  # type: ignore[arg-type]


def _build_entity_count_map(
    results: list[ExtractionResult],
) -> dict[str, int]:
    """Count how many chunks mention each entity (entity_id -> count).

    Uses the raw extraction results (before dedup) so an entity mentioned
    across 50 chunks scores degree=50 regardless of dedup.
    """
    counts: dict[str, int] = {}
    seen_per_chunk: dict[str, set[str]] = {}
    for r in results:
        seen = seen_per_chunk.setdefault(r.chunk_id, set())
        for e in r.entities:
            eid = hashlib.sha256(f"{e.label}\0{e.name}".encode("utf-8")).hexdigest()
            if eid not in seen:
                seen.add(eid)
                counts[eid] = counts.get(eid, 0) + 1
    return counts


def build_drift_report(
    blueprint: dict,
    source_id: str,
    results: list[ExtractionResult],
    resolved_entities: list[ResolvedEntity] | None = None,
    hub_threshold: int = 50,
) -> DriftReport:
    """Build a drift report from extraction results and resolution output.

    Args:
        blueprint: Validated blueprint dict.
        source_id: Source identifier for this run.
        results: Extraction results (one per chunk).
        resolved_entities: Resolved entities (output of resolve_batch).
        hub_threshold: Chunk count above which an entity is a hub candidate.

    Returns:
        DriftReport with all metrics computed.
    """
    ontology = blueprint.get("ontology", {})
    drift_cfg = blueprint.get("drift", {})
    patterns = _build_patterns_set(ontology)
    resolved = resolved_entities or []

    # --- Unmapped mentions ---
    unmapped: list[UnmappedMention] = []
    for result in results:
        for mention in result.unmapped_mentions:
            unmapped.append(
                UnmappedMention(chunk_id=result.chunk_id, text=mention)
            )

    total_entities = sum(len(r.entities) for r in results)
    total_unmapped = len(unmapped)
    unmapped_rate = (
        total_unmapped / max(1, total_unmapped + total_entities)
    )

    # --- Pattern violations ---
    violations: list[PatternViolation] = []
    for result in results:
        for rel in result.relations:
            key = (rel.source_label, rel.relation, rel.target_label)
            if key not in patterns:
                violations.append(
                    PatternViolation(
                        chunk_id=result.chunk_id,
                        relation=rel.relation,
                        source_label=rel.source_label,
                        target_label=rel.target_label,
                        reason=f"Pattern [{rel.source_label}]-[{rel.relation}]->[{rel.target_label}] not declared in blueprint",
                    )
                )

    total_relations = sum(len(r.relations) for r in results)
    total_violations = len(violations)
    violation_rate = total_violations / max(1, total_relations)

    # --- Vocabulary misses ---
    misses: list[VocabMiss] = []
    for re_entity in resolved:
        if re_entity.tier in ("embedding", "llm"):
            misses.append(
                VocabMiss(
                    entity_id=re_entity.entity_id,
                    label=re_entity.label,
                    surface_form=re_entity.surface_forms[0]
                    if re_entity.surface_forms
                    else re_entity.canonical_name,
                    tier=re_entity.tier,
                )
            )

    total_vocab_misses = len(misses)
    vocab_miss_rate = total_vocab_misses / max(1, len(resolved))

    # --- Hub candidates ---
    entity_counts = _build_entity_count_map(results)
    # Build a lookup from entity_id -> best resolved info
    entity_info: dict[str, tuple[str, str]] = {}  # eid -> (label, name)
    for re_entity in resolved:
        eid = re_entity.entity_id
        if eid not in entity_info:
            entity_info[eid] = (re_entity.label, re_entity.canonical_name)
    # Also add raw entity names for any not in resolved
    for r in results:
        for e in r.entities:
            eid = hashlib.sha256(f"{e.label}\0{e.name}".encode("utf-8")).hexdigest()
            if eid not in entity_info:
                entity_info[eid] = (e.label, e.name)

    hubs: list[HubCandidate] = []
    seen_hub_ids: set[str] = set()
    for eid, degree in entity_counts.items():
        if degree >= hub_threshold and eid not in seen_hub_ids:
            seen_hub_ids.add(eid)
            label, name = entity_info.get(eid, ("Entity", "unknown"))
            hubs.append(
                HubCandidate(
                    entity_id=eid,
                    label=label,
                    name=name,
                    degree=degree,
                    threshold=hub_threshold,
                )
            )

    hubs.sort(key=lambda h: h.degree, reverse=True)

    return DriftReport(
        blueprint_name=blueprint.get("name", "unknown"),
        blueprint_hash=blueprint.get("blueprint_hash", ""),
        source_id=source_id,
        unmapped_mention_rate=round(unmapped_rate, 4),
        unmapped_mentions=tuple(unmapped),
        pattern_violation_rate=round(violation_rate, 4),
        pattern_violations=tuple(violations),
        vocab_miss_rate=round(vocab_miss_rate, 4),
        vocab_misses=tuple(misses),
        hub_candidates=tuple(hubs),
        total_chunks=len(results),
        total_entities=total_entities,
        total_relations=total_relations,
        total_unmapped=total_unmapped,
        total_violations=total_violations,
        total_vocab_misses=total_vocab_misses,
        max_unmapped_mention_rate=drift_cfg.get("max_unmapped_mention_rate", 0.15),
        max_vocab_miss_rate=drift_cfg.get("max_vocabulary_miss_rate", 0.10),
        max_degree=drift_cfg.get("max_degree", 200),
    )


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


def print_report(report: DriftReport) -> str:
    """Format a drift report as a human-readable string for CLI output."""
    lines: list[str] = [
        f"Drift Report: {report.blueprint_name}",
        f"  source: {report.source_id}",
        f"  hash:   {report.blueprint_hash[:20]}...",
        f"  time:   {report.generated_at.isoformat()}",
        "",
        f"  Chunks:      {report.total_chunks}",
        f"  Entities:    {report.total_entities}",
        f"  Relations:   {report.total_relations}",
        "",
        f"  Unmapped mentions:    {report.total_unmapped} "
        f"({report.unmapped_mention_rate:.1%})  "
        + ("⚠" if report.unmapped_mention_rate > report.max_unmapped_mention_rate else "✓"),
        f"  Pattern violations:   {report.total_violations} "
        f"({report.pattern_violation_rate:.1%})",
        f"  Vocabulary misses:    {report.total_vocab_misses} "
        f"({report.vocab_miss_rate:.1%})  "
        + ("⚠" if report.vocab_miss_rate > report.max_vocab_miss_rate else "✓"),
        f"  Hub candidates:       {len(report.hub_candidates)}",
    ]

    if report.hub_candidates:
        lines.append("")
        lines.append("  Hubs (degree >= threshold):")
        for h in sorted(
            report.hub_candidates, key=lambda x: x.degree, reverse=True
        )[:10]:
            lines.append(f"    {h.name} ({h.label}) degree={h.degree}")

    if report.thresholds_exceeded:
        lines.append("")
        lines.append("  ⚠ Thresholds exceeded:")
        for msg in report.thresholds_exceeded:
            lines.append(f"    - {msg}")
        lines.append("  Consider re-opening the blueprint.")

    return "\n".join(lines)


__all__ = [
    "DriftReport",
    "HubCandidate",
    "PatternViolation",
    "UnmappedMention",
    "VocabMiss",
    "build_drift_report",
    "print_report",
]