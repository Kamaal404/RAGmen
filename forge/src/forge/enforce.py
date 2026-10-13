# ruff: noqa: E501
"""Closed-ontology enforcement: drop what the blueprint does not declare.

The prompt tells the extractor to use only declared labels and patterns, but a
prompt is a request, not a guarantee. This is the mechanical half: an entity
with an undeclared label, or a relation whose (source, RELATION, target) is not
in ontology.patterns, never reaches the store. No judgment involved, so no
human needs to read the dropped edges.

The drift report is built from the raw results, not these filtered ones, so a
rising violation rate stays visible as evidence for a blueprint revision.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from forge.extract.models import ExtractionResult  # noqa: TC001


@dataclass
class EnforcementResult:
    results: list[ExtractionResult]
    entities_dropped: int = 0
    relations_dropped: int = 0


def enforce_ontology(results: list[ExtractionResult], ontology: dict) -> EnforcementResult:
    """Filter extraction results down to the declared ontology.

    Only applies when ``ontology.closed`` is true, which the spec requires of
    every validated blueprint. The pass-through blueprint the CLI builds when
    none is found is not closed, and filtering against its empty type list
    would discard everything.
    """
    if not ontology.get("closed"):
        return EnforcementResult(results=list(results))

    labels = {nt["label"] for nt in ontology.get("node_types", [])}
    patterns = {tuple(p) for p in ontology.get("patterns", []) if len(p) == 3}

    out = EnforcementResult(results=[])
    for r in results:
        entities = [e for e in r.entities if e.label in labels]
        relations = [
            rel for rel in r.relations
            if (rel.source_label, rel.relation, rel.target_label) in patterns
        ]
        out.entities_dropped += len(r.entities) - len(entities)
        out.relations_dropped += len(r.relations) - len(relations)
        out.results.append(replace(r, entities=entities, relations=relations))
    return out


__all__ = ["EnforcementResult", "enforce_ontology"]
