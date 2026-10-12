"""Cost reporting for extraction runs.

Tracks per-chunk token usage and computes the savings from prompt caching
(the ontology block, which is 40-97% of input tokens, is charged once instead
of N times after the first chunk).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ChunkCostRow:
    """Cost and token usage for one chunk extraction."""

    chunk_id: str
    path: str
    model: str
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    entities: int
    relations: int
    cost_usd: float
    cost_without_cache_usd: float


@dataclass
class CostReport:
    """Aggregate cost report for an extraction run."""

    blueprint_hash: str
    source_uri: str
    model: str
    total_chunks: int
    total_prompt_tokens: int
    total_cached_tokens: int
    total_completion_tokens: int
    total_entities: int
    total_relations: int
    total_cost_usd: float
    total_without_cache_usd: float
    cache_savings_usd: float
    cache_savings_pct: float
    per_chunk: list[ChunkCostRow] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "blueprint_hash": self.blueprint_hash,
            "source": self.source_uri,
            "model": self.model,
            "total_chunks": self.total_chunks,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "cached_tokens": self.total_cached_tokens,
            "cache_hit_pct": round(
                self.total_cached_tokens / max(1, self.total_prompt_tokens) * 100, 1
            ),
            "total_entities": self.total_entities,
            "total_relations": self.total_relations,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "cache_savings_usd": round(self.cache_savings_usd, 4),
            "cache_savings_pct": round(self.cache_savings_pct, 1),
        }


def build_cost_report(
    rows: list[ChunkCostRow],
    blueprint_hash: str,
    source_uri: str,
) -> CostReport:
    """Aggregate per-chunk cost rows into a CostReport."""
    total_prompt = sum(r.prompt_tokens for r in rows)
    total_cached = sum(r.cached_prompt_tokens for r in rows)
    total_completion = sum(r.completion_tokens for r in rows)
    total_entities = sum(r.entities for r in rows)
    total_relations = sum(r.relations for r in rows)
    total_cost = sum(r.cost_usd for r in rows)
    total_no_cache = sum(r.cost_without_cache_usd for r in rows)

    return CostReport(
        blueprint_hash=blueprint_hash,
        source_uri=source_uri,
        model=rows[0].model if rows else "",
        total_chunks=len(rows),
        total_prompt_tokens=total_prompt,
        total_cached_tokens=total_cached,
        total_completion_tokens=total_completion,
        total_entities=total_entities,
        total_relations=total_relations,
        total_cost_usd=total_cost,
        total_without_cache_usd=total_no_cache,
        cache_savings_usd=round(total_no_cache - total_cost, 4),
        cache_savings_pct=round(
            (total_no_cache - total_cost) / max(0.001, total_no_cache) * 100, 1
        ),
        per_chunk=rows,
    )


__all__ = ["ChunkCostRow", "CostReport", "build_cost_report"]