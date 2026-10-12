"""Extraction pipeline — turn chunks into entities and relations.

The pipeline:
1. Build the ontology block (cache prefix) from the blueprint.
2. For each chunk, build an extraction prompt and call the provider.
3. Collect ExtractionResults and aggregate into a CostReport.
4. In dry-run mode, write prompts to disk without calling any LLM.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from forge.extract.cost import ChunkCostRow, CostReport, build_cost_report
from forge.extract.models import ExtractionResult
from forge.extract.prompts import build_ontology_block, estimate_prompt_cost
from forge.extract.provider import Extractor, extractor_for

from .cost import build_cost_report as _build_cost_report
from .models import ExtractionResult as _ExtractionResult
from .prompts import build_ontology_block as _build_ontology_block


def _calculate_row(
    result: ExtractionResult,
    ontology_block: str,
    chunk_path: str,
    model: str,
    is_first: bool,
) -> ChunkCostRow:
    """Build a cost row from an extraction result and prompt estimates."""
    if result.cost_usd > 0:
        # Real cost from provider
        cost = result.cost_usd
        cost_no_cache = cost + (result.cached_prompt_tokens / 1_000_000) * 3.0
    else:
        est = estimate_prompt_cost(
            ontology_block, result.chunk_id, chunk_path, model=model, cached=not is_first
        )
        cost = est["cost_usd"]
        cost_no_cache = estimate_prompt_cost(
            ontology_block, result.chunk_id, chunk_path, model=model, cached=False
        )["cost_usd"]

    return ChunkCostRow(
        chunk_id=result.chunk_id,
        path=chunk_path,
        model=model,
        prompt_tokens=result.prompt_tokens or estimate_prompt_cost(
            ontology_block, "", chunk_path, model=model, cached=not is_first
        )["new_tokens"],
        cached_prompt_tokens=result.cached_prompt_tokens,
        completion_tokens=result.completion_tokens,
        entities=len(result.entities),
        relations=len(result.relations),
        cost_usd=cost,
        cost_without_cache_usd=cost_no_cache,
    )


def run_extraction(
    blueprint: dict,
    chunks: list[dict],
    source_id: str,
    provider: str = "mock",
    dry_run: bool = False,
    dry_run_dir: str | Path | None = None,
    **provider_kwargs: Any,
) -> tuple[list[ExtractionResult], CostReport]:
    """Run extraction for all chunks of a source.

    Args:
        blueprint: Validated blueprint dict.
        chunks: List of dicts with keys: chunk_id, text, path.
        source_id: Source identifier.
        provider: Provider name ("mock", "openai").
        dry_run: If True, write prompts to disk without calling any LLM.
        dry_run_dir: Directory for dry-run artifacts.
        **provider_kwargs: Passed to the provider constructor.

    Returns:
        (list of ExtractionResult, CostReport)
    """
    ontology = blueprint.get("ontology", {})
    provenance = blueprint.get("provenance", {})
    ontology_block = build_ontology_block(ontology)

    extractor = extractor_for(provider, **provider_kwargs)

    results: list[ExtractionResult] = []
    cost_rows: list[ChunkCostRow] = []
    dry_dir = Path(dry_run_dir or "forge-dry-run")
    source_uri = blueprint.get("name", "unknown")

    for i, chunk in enumerate(chunks):
        chunk_id = chunk["chunk_id"]
        chunk_text = chunk["text"]
        chunk_path = chunk.get("path", "")

        if dry_run:
            extractor.dry_run(
                chunk_text, chunk_path, chunk_id, source_id,
                ontology_block, out_dir=dry_dir,
            )
            result = ExtractionResult(
                chunk_id=chunk_id,
                source_id=source_id,
                model=extractor.model,
            )
        else:
            result = extractor.extract(
                chunk_text, chunk_path, chunk_id, source_id,
                ontology_block, is_first_chunk=(i == 0),
            )

        results.append(result)
        cost_rows.append(_calculate_row(result, ontology_block, chunk_path, extractor.model, i == 0))

    report = build_cost_report(cost_rows, blueprint.get("blueprint_hash", ""), source_uri)
    return results, report


def save_dry_run_manifest(
    results: list[ExtractionResult],
    report: CostReport,
    blueprint: dict,
    out_dir: str | Path,
) -> Path:
    """Write a dry-run manifest with per-chunk prompts and aggregate costs."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "blueprint": {"name": blueprint.get("name"), "hash": blueprint.get("blueprint_hash")},
        "report": report.summary,
        "total_chunks": len(results),
    }
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


__all__ = [
    "ExtractionResult",
    "CostReport",
    "build_ontology_block",
    "extractor_for",
    "run_extraction",
    "save_dry_run_manifest",
]