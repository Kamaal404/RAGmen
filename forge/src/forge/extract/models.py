"""Dataclasses for the extraction output — entities, relations, and provenance.

Every extraction run produces a list of ExtractionResult, one per chunk.
Each result carries the chunk's verbatim extractions plus metadata for the
cost report and downstream resolution/embedding stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ExtractedProperty:
    """A single property on an extracted entity."""

    name: str
    value: str | int | float | bool


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    """An entity extracted from a chunk, typed against the blueprint ontology."""

    label: str
    name: str
    properties: list[ExtractedProperty] = field(default_factory=list)
    quote: str = ""
    char_start: int = 0
    char_end: int = 0
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    """A typed relationship between two entities within one chunk."""

    source_label: str
    source_name: str
    relation: str
    target_label: str
    target_name: str
    properties: list[ExtractedProperty] = field(default_factory=list)
    quote: str = ""
    char_start: int = 0
    char_end: int = 0
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """All entities and relations extracted from one chunk."""

    chunk_id: str
    source_id: str
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    unmapped_mentions: list[str] = field(default_factory=list)
    extracted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


__all__ = [
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractedProperty",
    "ExtractionResult",
]