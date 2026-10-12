# ruff: noqa: E501
"""Entity resolver — deduplicate extracted entities against the blueprint vocabulary.

Design:
  Every entity extracted from a chunk is resolved against the blueprint's
  vocabularies and resolution strategies. The result is a ResolvedEntity with
  a canonical name and a deterministic entity_id.

  Resolution is *scoped*: only new chunks' entities are resolved against the
  existing canonical set. The entire graph is never re-resolved. This prevents
  drift where a re-run re-merges things a human deliberately separated.

Tiers (cheapest first):
  1. Exact match — hash lookup against vocabulary entries
  2. Fuzzy match — RapidFuzz against vocabulary entries (threshold from blueprint)
  3. Embedding match — stub for phase 4
  4. LLM closed-form — stub for phase 4
  5. "new" — entity becomes its own canonical entry
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from forge.extract.models import ExtractedEntity, ExtractionResult


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    """An entity resolved to a canonical name."""

    canonical_name: str
    label: str
    surface_forms: tuple[str, ...] = ()
    tier: str = "exact"  # exact | fuzzy | embedding | llm | new | existing
    entity_id: str = ""

    def __post_init__(self) -> None:
        if not self.entity_id:
            object.__setattr__(
                self,
                "entity_id",
                hashlib.sha256(
                    f"{self.label}\0{self.canonical_name}".encode("utf-8")
                ).hexdigest(),
            )


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Controlled vocabulary for one node type.

    Maps canonical names to their allowed surface forms and resolution config.
    """

    label: str
    entries: dict[str, list[str]]  # canonical_name -> [surface_form, ...]
    fuzzy_threshold: float = 0.85

    @classmethod
    def from_blueprint_ontology(
        cls, ontology: dict, label: str
    ) -> Vocabulary | None:
        """Build a Vocabulary from the blueprint's vocabularies section.

        Returns None if the label has no declared vocabulary.
        """
        v = ontology.get("vocabularies", {}).get(label)
        if v is None:
            return None

        # For now, try to load entries from the blueprint itself.
        # In production these come from a CSV file (v.source).
        # Fall back to empty entries — resolve will just pass through.
        entries: dict[str, list[str]] = {}
        raw_entries = v.get("entries", v.get("values", []))
        for entry in raw_entries:
            if isinstance(entry, dict):
                canon = entry.get("canonical", entry.get("name", ""))
                forms = entry.get("surface_forms", entry.get("aliases", []))
                if canon:
                    entries[canon] = forms
            elif isinstance(entry, str):
                entries[entry] = []

        threshold = v.get("fuzzy_threshold", 0.85)
        return cls(label=label, entries=entries, fuzzy_threshold=threshold)

    @classmethod
    def vocabularies_from_blueprint(
        cls, blueprint: dict
    ) -> dict[str, Vocabulary]:
        """Load all vocabularies declared in the blueprint."""
        ontology = blueprint.get("ontology", {})
        v = ontology.get("vocabularies", {})
        result: dict[str, Vocabulary] = {}
        for label in v:
            vocab = cls.from_blueprint_ontology(ontology, label)
            if vocab is not None:
                result[label] = vocab
        return result


# ---------------------------------------------------------------------------
# EntityResolver
# ---------------------------------------------------------------------------


class EntityResolver:
    """Scoped entity resolution against the blueprint vocabulary.

    Usage::

        resolver = EntityResolver(blueprint)
        resolved = resolver.resolve(extraction_results, existing_entity_ids)
    """

    def __init__(self, blueprint: dict) -> None:
        self.vocabularies = Vocabulary.vocabularies_from_blueprint(blueprint)
        self.resolution_config = blueprint.get("ontology", {}).get("resolution", [])

    def resolve(
        self,
        entities: list[ExtractedEntity],
        existing_entity_ids: set[str] | None = None,
    ) -> list[ResolvedEntity]:
        """Resolve *entities* against the vocabulary.

        Args:
            entities: Extracted entities (from one or more chunks).
            existing_entity_ids: IDs of already-resolved entities in the store.
                Entities matching an existing ID are marked tier "existing"
                and returned as-is.

        Returns:
            One ResolvedEntity per input entity.
        """
        existing = existing_entity_ids or set()
        resolved: list[ResolvedEntity] = []

        for entity in entities:
            raw_id = self._raw_id(entity.label, entity.name)

            # Tier 0: already in the store
            if raw_id in existing:
                resolved.append(
                    ResolvedEntity(
                        canonical_name=entity.name,
                        label=entity.label,
                        surface_forms=(entity.name,),
                        tier="existing",
                        entity_id=raw_id,
                    )
                )
                continue

            # Tier 1: exact match
            matched = self._exact_match(entity.name, entity.label)
            if matched is not None:
                resolved.append(
                    ResolvedEntity(
                        canonical_name=matched,
                        label=entity.label,
                        surface_forms=(entity.name,),
                        tier="exact",
                    )
                )
                continue

            # Tier 2: fuzzy match
            fuzzy_match = self._fuzzy_match(entity.name, entity.label)
            if fuzzy_match is not None:
                resolved.append(
                    ResolvedEntity(
                        canonical_name=fuzzy_match,
                        label=entity.label,
                        surface_forms=(entity.name,),
                        tier="fuzzy",
                    )
                )
                continue

            # Tier 3: embedding match (stub for phase 4)
            # Tier 4: LLM closed-form resolve (stub for phase 4)

            # Tier 5: new entity
            resolved.append(
                ResolvedEntity(
                    canonical_name=entity.name,
                    label=entity.label,
                    surface_forms=(entity.name,),
                    tier="new",
                )
            )

        return resolved

    # ------------------------------------------------------------------
    # Resolution tiers
    # ------------------------------------------------------------------

    def _exact_match(self, name: str, label: str) -> str | None:
        """Check if *name* matches a vocabulary entry exactly.

        Checks both the canonical name itself and all declared surface forms.
        """
        vocab = self.vocabularies.get(label)
        if vocab is None:
            return None

        name_lower = name.lower().strip()
        for canonical, forms in vocab.entries.items():
            if canonical.lower() == name_lower:
                return canonical
            for form in forms:
                if form.lower() == name_lower:
                    return canonical
        return None

    def _fuzzy_match(self, name: str, label: str) -> str | None:
        """Fuzzy match *name* against vocabulary entries using RapidFuzz.

        Returns the best-matching canonical name if above threshold.
        """
        vocab = self.vocabularies.get(label)
        if vocab is None:
            return None

        try:
            from rapidfuzz import fuzz  # noqa: I001
        except ImportError:
            return None

        threshold = vocab.fuzzy_threshold
        name_stripped = name.strip().lower()
        best: tuple[float, str] = (0.0, "")

        for canonical, forms in vocab.entries.items():
            # Compare against canonical
            ratio = fuzz.ratio(name_stripped, canonical.lower().strip())
            if ratio > best[0]:
                best = (ratio, canonical)
            # Compare against surface forms
            for form in forms:
                ratio = fuzz.ratio(name_stripped, form.lower().strip())
                if ratio > best[0]:
                    best = (ratio, canonical)
            # Compare against individual words
            for word in canonical.split():
                if len(word) > 3:
                    ratio = fuzz.partial_ratio(name_stripped, word.lower())
                    if ratio > best[0]:
                        best = (ratio, canonical)

        if best[0] >= threshold:
            return best[1]
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_id(label: str, name: str) -> str:
        return hashlib.sha256(
            f"{label}\0{name}".encode("utf-8")
        ).hexdigest()


# ---------------------------------------------------------------------------
# Batch resolver
# ---------------------------------------------------------------------------


def resolve_batch(
    results: list[ExtractionResult],
    blueprint: dict,
    existing_entity_ids: set[str] | None = None,
) -> list[ResolvedEntity]:
    """Resolve all entities from a batch of extraction results.

    Args:
        results: Extraction results (one per chunk).
        blueprint: Validated blueprint dict.
        existing_entity_ids: Set of already-resolved entity IDs in the store.

    Returns:
        Flat list of resolved entities (deduplicated by entity_id).
    """
    resolver = EntityResolver(blueprint)

    # Collect all unique entities across all chunks
    seen: dict[str, ExtractedEntity] = {}
    for result in results:
        for entity in result.entities:
            eid = resolver._raw_id(entity.label, entity.name)  # noqa: SLF001
            if eid not in seen:
                seen[eid] = entity

    raw_entities = list(seen.values())
    resolved = resolver.resolve(raw_entities, existing_entity_ids)

    # Deduplicate resolved entities by entity_id (same canonical form
    # reached from different surface forms)
    deduped: dict[str, ResolvedEntity] = {}
    for r in resolved:
        if r.entity_id in deduped:
            old = deduped[r.entity_id]
            deduped[r.entity_id] = ResolvedEntity(
                canonical_name=old.canonical_name,
                label=old.label,
                surface_forms=tuple(set(old.surface_forms + r.surface_forms)),
                tier=old.tier,
                entity_id=old.entity_id,
            )
        else:
            deduped[r.entity_id] = r

    return list(deduped.values())


# ---------------------------------------------------------------------------
# Scoped re-resolution: given old + new extractions for the same source,
# find what changed and resolve only new/changed entities.
# ---------------------------------------------------------------------------


def scoped_re_resolve(
    old_results: list[ExtractionResult],
    new_results: list[ExtractionResult],
    blueprint: dict,
    existing_entity_ids: set[str] | None = None,
) -> tuple[list[ResolvedEntity], list[ExtractionResult]]:
    """Re-resolve only entities that are new or changed.

    Compares old vs new extraction results for the same source.
    Entities identical in both are skipped. Only new/changed entities
    go through resolution.

    Returns:
        (resolved_entities_for_changed_chunks, changed_extraction_results)
    """
    # Build lookup of old chunk_id -> entity set
    old_chunk_entities: dict[str, set[str]] = {}
    for r in old_results:
        ids = set()
        for e in r.entities:
            ids.add(hashlib.sha256(f"{e.label}\0{e.name}".encode("utf-8")).hexdigest())
        old_chunk_entities[r.chunk_id] = ids

    changed_results: list[ExtractionResult] = []
    changed_entities: list[ExtractedEntity] = []

    for r in new_results:
        old_ids = old_chunk_entities.get(r.chunk_id, set())
        new_ids = set()
        for e in r.entities:
            eid = hashlib.sha256(f"{e.label}\0{e.name}".encode("utf-8")).hexdigest()
            new_ids.add(eid)
            if eid not in old_ids:
                changed_entities.append(e)

        # If the chunk itself is new, keep the whole result
        if r.chunk_id not in old_chunk_entities:
            changed_results.append(r)
        # If entities changed, keep the result for changed entities
        elif new_ids != old_ids:
            changed_results.append(r)

    if not changed_entities:
        return [], changed_results

    resolver = EntityResolver(blueprint)
    resolved = resolver.resolve(changed_entities, existing_entity_ids)
    return resolved, changed_results


__all__ = [
    "EntityResolver", "ResolvedEntity", "Vocabulary",
    "resolve_batch", "scoped_re_resolve",
]