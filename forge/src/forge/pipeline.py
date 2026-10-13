# ruff: noqa: E501
"""Forge pipeline — orchestrate resolve, embed, write, and validate.

Phase 3 end-to-end flow::

    extraction results
        -> enforce (drop labels and patterns the blueprint does not declare)
        -> resolve (deduplicate against vocab)
        -> embed (generate vectors for chunks + entity cards)
        -> write to store (entities, relations, chunks, embeddings)
        -> validate (emit drift report)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.embed import Embedder, MockEmbedder, build_embedding_targets, embed_for
from forge.enforce import enforce_ontology
from forge.extract.cost import CostReport
from forge.extract.models import ExtractionResult
from forge.registry.models import Chunk, Source
from forge.report import DriftReport, build_drift_report, print_report
from forge.resolve import ResolvedEntity, resolve_batch
from forge.store import GraphStore, store_for


@dataclass
class PipelineResult:
    """Result of a full pipeline run for one source."""

    source_id: str
    resolved_entities: list[ResolvedEntity] = field(default_factory=list)
    drift_report: DriftReport | None = None
    embeddings_written: int = 0
    entities_written: int = 0
    relations_written: int = 0
    chunks_written: int = 0
    entities_dropped: int = 0
    relations_dropped: int = 0
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def _embed_kwargs(blueprint: dict, backend: str, given: dict[str, Any] | None) -> dict[str, Any]:
    """Embedder settings from the blueprint, overridden by anything passed in.

    The blueprint's model and dimensions are what the vector index is built
    for. An embedder that ignores them still produces vectors, just ones every
    search silently misses.
    """
    em = blueprint.get("embedding", {})
    kwargs: dict[str, Any] = {}
    if em.get("dimensions"):
        kwargs["dimensions"] = em["dimensions"]
    if backend == "local" and em.get("model"):
        kwargs["model_name"] = em["model"]
    kwargs.update(given or {})
    return kwargs


def _existing_entity_ids(store: GraphStore) -> set[str]:
    """Extract existing entity IDs from the store.

    For MemoryStore, use its entity dict. For Neo4j, run a query.
    Best-effort — if not supported, return empty set.
    """
    if hasattr(store, "entities"):
        return set(store.entities.keys())
    return set()


def run_pipeline(
    blueprint: dict,
    source: Source,
    chunks: list[Chunk],
    extraction_results: list[ExtractionResult],
    store_backend: str = "memory",
    embed_backend: str = "mock",
    store_kwargs: dict[str, Any] | None = None,
    embed_kwargs: dict[str, Any] | None = None,
    hub_threshold: int = 50,
    dry_run: bool = False,
) -> PipelineResult:
    """Run the full resolve -> embed -> write -> validate pipeline.

    Args:
        blueprint: Validated blueprint dict.
        source: Source record for the ingested document.
        chunks: Chunks produced by chunk_document.
        extraction_results: Extraction results (one per chunk).
        store_backend: "memory" or "neo4j".
        embed_backend: "mock" or "local" (sentence-transformers).
        store_kwargs: Passed to store_for().
        embed_kwargs: Passed to embed_for().
        hub_threshold: Degree threshold for hub detection.
        dry_run: If True, skip writing to store.

    Returns:
        PipelineResult with all metrics.
    """
    result = PipelineResult(source_id=source.source_id)
    store_kwargs = store_kwargs or {}
    embed_kwargs = _embed_kwargs(blueprint, embed_backend, embed_kwargs)

    # --- 1. Open store ---
    try:
        store: GraphStore = store_for(store_backend, **store_kwargs)
    except Exception as e:
        result.errors.append(f"Failed to open store: {e}")
        return result

    try:
        # --- 2. Enforce the closed ontology, then resolve ---
        enforced = enforce_ontology(extraction_results, blueprint.get("ontology", {}))
        result.entities_dropped = enforced.entities_dropped
        result.relations_dropped = enforced.relations_dropped
        kept = enforced.results

        existing = _existing_entity_ids(store)
        resolved = resolve_batch(kept, blueprint, existing)
        result.resolved_entities = resolved

        # --- 3. Build embedding targets ---
        chunks_data = [
            {"chunk_id": c.chunk_id, "text": c.text, "path": c.path}
            for c in chunks
        ]
        embedding_targets = build_embedding_targets(
            blueprint, chunks_data, resolved
        )

        # --- 4. Embed ---
        embedder: Embedder = embed_for(embed_backend, **embed_kwargs)
        embeddings: dict[str, list[float]] = {}
        for target_name, text, content_key in embedding_targets:
            try:
                vec = embedder.embed(text, key=content_key)
                embeddings[content_key] = vec
            except Exception as e:
                result.errors.append(
                    f"Embedding failed for {target_name}/{content_key}: {e}"
                )

        result.embeddings_written = len(embeddings)

        # --- 5. Write to store ---
        if not dry_run:
            try:
                store.create_indexes()
                store.set_blueprint_active(
                    blueprint.get("blueprint_hash", "")
                )

                # Write chunks
                for chunk in chunks:
                    try:
                        store.write_chunk(chunk, source)
                        result.chunks_written += 1
                    except Exception as e:
                        result.errors.append(
                            f"Failed to write chunk {chunk.chunk_id}: {e}"
                        )

                # Write entities + MENTIONS edges
                for er in kept:
                    for entity in er.entities:
                        try:
                            store.write_entity(
                                entity,
                                er.chunk_id,
                                source.source_id,
                                blueprint.get("blueprint_hash", ""),
                            )
                            result.entities_written += 1
                        except Exception as e:
                            result.errors.append(
                                f"Failed to write entity {entity.name}: {e}"
                            )

                # Write relations
                for er in kept:
                    for relation in er.relations:
                        try:
                            store.write_relation(
                                relation,
                                er.chunk_id,
                                blueprint.get("blueprint_hash", ""),
                            )
                            result.relations_written += 1
                        except Exception as e:
                            result.errors.append(
                                f"Failed to write relation {relation.relation}: {e}"
                            )

                # Write embeddings
                for content_key, vec in embeddings.items():
                    try:
                        # Determine node type from content_key prefix
                        if content_key.startswith("chunk:"):
                            node_id = content_key[len("chunk:"):]
                            store.write_embedding(
                                node_id, "Chunk", vec,
                                blueprint.get("blueprint_hash", ""),
                            )
                        elif content_key.startswith("entity:"):
                            entity_id = content_key[len("entity:"):]
                            store.write_embedding(
                                entity_id, "Entity", vec,
                                blueprint.get("blueprint_hash", ""),
                            )
                    except Exception as e:
                        result.errors.append(
                            f"Failed to write embedding {content_key}: {e}"
                        )

            except Exception as e:
                result.errors.append(f"Store write phase failed: {e}")

        # --- 6. Drift report ---
        try:
            drift = build_drift_report(
                blueprint=blueprint,
                source_id=source.source_id,
                results=extraction_results,
                resolved_entities=resolved,
                hub_threshold=hub_threshold,
            )
            result.drift_report = drift
        except Exception as e:
            result.errors.append(f"Failed to build drift report: {e}")

    finally:
        store.close()

    # --- 6b. Drift threshold enforcement ---
    if result.drift_report and result.drift_report.thresholds_exceeded:
        drift_cfg = blueprint.get("drift", {})
        if drift_cfg.get("fail_on_exceeded", False):
            result.errors.append(
                f"Drift thresholds exceeded: {', '.join(result.drift_report.thresholds_exceeded)}. "
                f"Set drift.fail_on_exceeded=false in the blueprint to allow."
            )

    # --- Summary ---
    result.summary = {
        "source_id": source.source_id,
        "chunks": len(chunks),
        "entities_resolved": len(resolved),
        "embeddings_computed": result.embeddings_written,
        "entities_written": result.entities_written,
        "relations_written": result.relations_written,
        "entities_dropped": result.entities_dropped,
        "relations_dropped": result.relations_dropped,
        "chunks_written": result.chunks_written,
        "errors": len(result.errors),
        "drift": result.drift_report.thresholds_exceeded
        if result.drift_report
        else [],
    }

    return result


def run_update_pipeline(
    blueprint: dict,
    source: Source,
    old_extraction_results: list[ExtractionResult],
    new_chunks: list[Chunk],
    new_extraction_results: list[ExtractionResult],
    store_backend: str = "memory",
    embed_backend: str = "mock",
    store_kwargs: dict[str, Any] | None = None,
    embed_kwargs: dict[str, Any] | None = None,
    hub_threshold: int = 50,
    dry_run: bool = False,
) -> PipelineResult:
    """Incremental update: re-chunk and re-extract only changed parts of a source.

    Uses scoped_re_resolve to skip entities that haven't changed, and
    only writes new/changed chunks, entities, relations, and embeddings.
    """
    from forge.resolve import scoped_re_resolve

    result = PipelineResult(source_id=source.source_id)
    store_kwargs = store_kwargs or {}
    embed_kwargs = _embed_kwargs(blueprint, embed_backend, embed_kwargs)

    # Enforce both sides, so a dropped edge is not seen as a change.
    ontology = blueprint.get("ontology", {})
    old_extraction_results = enforce_ontology(old_extraction_results, ontology).results
    enforced = enforce_ontology(new_extraction_results, ontology)
    result.entities_dropped = enforced.entities_dropped
    result.relations_dropped = enforced.relations_dropped
    raw_new_results = new_extraction_results
    new_extraction_results = enforced.results

    try:
        store: GraphStore = store_for(store_backend, **store_kwargs)
    except Exception as e:
        result.errors.append(f"Failed to open store: {e}")
        return result

    try:
        # Scoped resolution — only resolve new/changed entities
        existing = _existing_entity_ids(store)
        resolved, changed_results = scoped_re_resolve(
            old_extraction_results, new_extraction_results,
            blueprint, existing,
        )
        result.resolved_entities = resolved

        # Build embedding targets for changed chunks + resolved entities
        changed_chunks = [c for c in new_chunks
                          if any(r.chunk_id == c.chunk_id for r in changed_results)]
        chunks_data = [
            {"chunk_id": c.chunk_id, "text": c.text, "path": c.path}
            for c in changed_chunks
        ]
        embedding_targets = build_embedding_targets(blueprint, chunks_data, resolved)

        # Embed
        embedder: Embedder = embed_for(embed_backend, **embed_kwargs)
        embeddings: dict[str, list[float]] = {}
        for target_name, text, content_key in embedding_targets:
            try:
                vec = embedder.embed(text, key=content_key)
                embeddings[content_key] = vec
            except Exception as e:
                result.errors.append(f"Embedding failed for {target_name}/{content_key}: {e}")
        result.embeddings_written = len(embeddings)

        if not dry_run:
            try:
                store.create_indexes()
                store.set_blueprint_active(blueprint.get("blueprint_hash", ""))

                # Write only changed chunks
                for chunk in changed_chunks:
                    try:
                        store.write_chunk(chunk, source)
                        result.chunks_written += 1
                    except Exception as e:
                        result.errors.append(f"Failed to write chunk {chunk.chunk_id}: {e}")

                # Write entities from changed results
                for er in changed_results:
                    for entity in er.entities:
                        try:
                            store.write_entity(entity, er.chunk_id, source.source_id,
                                               blueprint.get("blueprint_hash", ""))
                            result.entities_written += 1
                        except Exception as e:
                            result.errors.append(f"Failed to write entity {entity.name}: {e}")

                # Write relations from changed results
                for er in changed_results:
                    for relation in er.relations:
                        try:
                            store.write_relation(relation, er.chunk_id,
                                                 blueprint.get("blueprint_hash", ""))
                            result.relations_written += 1
                        except Exception as e:
                            result.errors.append(f"Failed to write relation {relation.relation}: {e}")

                # Write embeddings
                for content_key, vec in embeddings.items():
                    try:
                        if content_key.startswith("chunk:"):
                            store.write_embedding(content_key[len("chunk:"):], "Chunk", vec,
                                                   blueprint.get("blueprint_hash", ""))
                        elif content_key.startswith("entity:"):
                            store.write_embedding(content_key[len("entity:"):], "Entity", vec,
                                                   blueprint.get("blueprint_hash", ""))
                    except Exception as e:
                        result.errors.append(f"Failed to write embedding {content_key}: {e}")

            except Exception as e:
                result.errors.append(f"Store write phase failed: {e}")

        # Drift report uses all new results, not just changed ones
        try:
            drift = build_drift_report(
                blueprint=blueprint,
                source_id=source.source_id,
                results=raw_new_results,
                resolved_entities=resolved,
                hub_threshold=hub_threshold,
            )
            result.drift_report = drift
        except Exception as e:
            result.errors.append(f"Failed to build drift report: {e}")

    finally:
        store.close()

    result.summary = {
        "source_id": source.source_id,
        "chunks_changed": len(changed_chunks),
        "entities_resolved_new": len(resolved),
        "embeddings_computed": result.embeddings_written,
        "entities_written": result.entities_written,
        "relations_written": result.relations_written,
        "entities_dropped": result.entities_dropped,
        "relations_dropped": result.relations_dropped,
        "chunks_written": result.chunks_written,
        "errors": len(result.errors),
        "drift": result.drift_report.thresholds_exceeded if result.drift_report else [],
    }
    return result


def run_pipeline_report(result: PipelineResult) -> str:
    """Format a pipeline result as human-readable text."""
    lines: list[str] = [
        f"Pipeline: {result.source_id}",
        f"  Chunks:          {result.chunks_written}",
        f"  Entities:        {result.entities_written} (resolved: {len(result.resolved_entities)})",
        f"  Relations:       {result.relations_written}",
        f"  Embeddings:      {result.embeddings_written}",
    ]
    if result.entities_dropped or result.relations_dropped:
        lines.append(
            f"  Dropped:         {result.entities_dropped} entities, "
            f"{result.relations_dropped} relations outside the declared ontology"
        )
    if result.drift_report:
        lines.append("")
        lines.append(print_report(result.drift_report))
    if result.errors:
        lines.append("")
        lines.append(f"  Errors ({len(result.errors)}):")
        for err in result.errors[:10]:
            lines.append(f"    - {err}")
    return "\n".join(lines)


__all__ = ["PipelineResult", "run_pipeline", "run_update_pipeline", "run_pipeline_report"]