#! /usr/bin/env python3
"""Forge CLI — add, list, retract, inspect, and extract.

Usage:
    forge add <path> --blueprint <blueprint.yaml> [--owner <name>] [--visibility <level>]
    forge list [--status <filter>]
    forge status <source_id>
    forge retract <source_id>
    forge chunks <source_id> [--json]
    forge blueprint <blueprint.yaml> [--check]
    forge extract <source_id> [--provider mock|openai] [--dry-run] [--dry-run-dir <dir>]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from forge.blueprint import BlueprintError, BlueprintNotFoundError, load_blueprint
from forge.chunking import Chunk, chunk_document
from forge.extract import run_extraction, save_dry_run_manifest
from forge.loaders import load_source, loader_for
from forge.registry import RegistryStore, Source


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------

def _content_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> None:
    """Register a source and run it through chunking."""
    path = Path(args.path)
    if not path.exists():
        sys.exit(f"not found: {path}")

    bp_path = Path(args.blueprint)
    try:
        bp = load_blueprint(bp_path)
    except BlueprintNotFoundError:
        sys.exit(f"blueprint not found: {bp_path}")
    except BlueprintError as e:
        for err in e.errors:
            print(f"ERROR  {err}", file=sys.stderr)
        sys.exit("blueprint is invalid")

    # Load source text
    try:
        mime, text = load_source(path)
    except ValueError as e:
        sys.exit(str(e))

    content_hash = _content_hash(path)

    # Check for existing source with same URI + content hash (dedup)
    store_path = args.store_path or Path.cwd() / "forge.db"

    with RegistryStore(store_path) as store:
        existing = store.get_source_by_uri(str(path))
        if existing and existing.content_hash == content_hash and existing.status != "retracted":
            print(f"unchanged, 0 new chunks  ({existing.source_id})")
            return

        # Create source record
        source = Source(
            uri=str(path),
            content_hash=content_hash,
            mime=mime,
            blueprint_version=bp["blueprint_version"],
            blueprint_hash=bp["blueprint_hash"],
            added_by=args.owner or "",
            visibility=args.visibility or "public",
            status="chunking",
        )
        source = store.add_source(source)

        # Create ingest job
        from forge.registry import IngestJob
        job = store.create_job(
            IngestJob(
                source_id=source.source_id,
                blueprint_hash=bp["blueprint_hash"],
            )
        )

        # Chunk the document
        structure = bp.get("structure", {})
        chunks = chunk_document(text, structure, source.source_id, bp["blueprint_hash"])

        # Store chunks first (they need to exist before job_chunk_status FK)
        new_chunks: list[Chunk] = []
        for c in chunks:
            if not store.chunk_exists(c.chunk_id):
                new_chunks.append(c)

        if new_chunks:
            count = store.add_chunks(new_chunks)
        else:
            count = 0

        # Now mark them in the job ledger
        store.mark_chunks_pending(job.job_id, (c.chunk_id for c in chunks))
        for c in new_chunks:
            store.set_chunk_status(job.job_id, c.chunk_id, "written")
        for c in chunks:
            if store.chunk_exists(c.chunk_id) and c not in new_chunks:
                store.set_chunk_status(job.job_id, c.chunk_id, "existing")

        # Run extraction (dry-run by default if no API key)
        chunks_data = [
            {"chunk_id": c.chunk_id, "text": c.text, "path": c.path}
            for c in chunks
        ]
        if args.dry_run or args.provider == "mock":
            dry_dir = Path("forge-dry-run") / source.source_id[:8]
            results, report = run_extraction(
                blueprint=bp,
                chunks=chunks_data,
                source_id=source.source_id,
                provider=args.provider,
                dry_run=args.dry_run,
                dry_run_dir=dry_dir,
            )
            if args.dry_run:
                save_dry_run_manifest(results, report, bp, dry_dir)
                s = report.summary
                print(f"cost estimate: ${s['total_cost_usd']:.4f} "
                      f"({s['cache_savings_pct']}% saved by prompt caching)")
                print(f"dry-run prompts: {dry_dir}/")
        else:
            results, report = run_extraction(
                blueprint=bp,
                chunks=chunks_data,
                source_id=source.source_id,
                provider=args.provider,
            )
            n_entities = sum(len(r.entities) for r in results)
            n_relations = sum(len(r.relations) for r in results)
            print(f"extracted {n_entities} entities, {n_relations} relations")

        store.update_source_status(source.source_id, "ready")

        # Complete the job
        store.update_job_status(
            job.job_id, "completed",
            completed_chunks=len(new_chunks),
            completed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

        added = len(new_chunks)
        total = len(chunks)
        if existing:
            print(f"updated, {added} new chunk(s) of {total}  ({source.source_id})")
        else:
            print(f"added {added} chunk(s) of {total}  ({source.source_id})")


def cmd_list(args: argparse.Namespace) -> None:
    """List registered sources."""
    store_path = args.store_path or Path.cwd() / "forge.db"
    with RegistryStore(store_path) as store:
        sources = store.list_sources()
        if not sources:
            print("no sources registered")
            return

        status_filter = args.status
        for s in sources:
            if status_filter and s.status != status_filter:
                continue
            print(f"  {s.source_id:<36} {s.status:<10} {s.uri}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show detailed source status."""
    store_path = args.store_path or Path.cwd() / "forge.db"
    with RegistryStore(store_path) as store:
        source = store.get_source(args.source_id)
        if source is None:
            sys.exit(f"source not found: {args.source_id}")

        print(f"source_id    : {source.source_id}")
        print(f"uri          : {source.uri}")
        print(f"mime         : {source.mime}")
        print(f"status       : {source.status}")
        print(f"chunk_count  : {source.chunk_count}")
        print(f"blueprint    : {source.blueprint_version} ({source.blueprint_hash[:16]}...)")
        print(f"added_by     : {source.added_by}")
        print(f"visibility   : {source.visibility}")
        print(f"license      : {source.license}")
        print(f"cost_usd     : {source.cost_usd}")


def cmd_retract(args: argparse.Namespace) -> None:
    """Remove a source and its chunks."""
    store_path = args.store_path or Path.cwd() / "forge.db"
    with RegistryStore(store_path) as store:
        if store.retract_source(args.source_id):
            print(f"retracted: {args.source_id}")
        else:
            sys.exit(f"source not found or already retracted: {args.source_id}")


def cmd_chunks(args: argparse.Namespace) -> None:
    """List chunks for a source."""
    import json

    store_path = args.store_path or Path.cwd() / "forge.db"
    with RegistryStore(store_path) as store:
        source = store.get_source(args.source_id)
        if source is None:
            sys.exit(f"source not found: {args.source_id}")

        chunks = store.get_chunks_for_source(args.source_id)
        if args.json:
            data = [
                {
                    "chunk_id": c.chunk_id,
                    "path": c.path,
                    "page": c.page,
                    "chars": c.char_end - c.char_start,
                    "preview": c.text[:80],
                }
                for c in chunks
            ]
            json.dump(data, sys.stdout, indent=2)
            print()
        else:
            for c in chunks:
                preview = c.text[:60].replace("\n", " ")
                print(f"  {c.chunk_id[:16]}  {c.path:<30}  [{c.char_start}-{c.char_end}]  {preview}")


def cmd_extract(args: argparse.Namespace) -> None:
    """Run extraction on a registered source."""
    store_path = args.store_path or Path.cwd() / "forge.db"
    dry_dir = Path(args.dry_run_dir)

    with RegistryStore(store_path) as store:
        source = store.get_source(args.source_id)
        if source is None:
            sys.exit(f"source not found: {args.source_id}")

        chunks_data = [
            {"chunk_id": c.chunk_id, "text": c.text, "path": c.path}
            for c in store.get_chunks_for_source(args.source_id)
        ]

        if not chunks_data:
            sys.exit("no chunks found for this source")

        print(f"extracting {len(chunks_data)} chunk(s) from {source.uri} "
              f"(provider: {args.provider})")

        results, report = run_extraction(
            blueprint={"ontology": {}, "blueprint_hash": source.blueprint_hash, "name": source.uri},
            chunks=chunks_data,
            source_id=source.source_id,
            provider=args.provider,
            dry_run=args.dry_run,
            dry_run_dir=dry_dir,
        )

        if args.dry_run:
            save_dry_run_manifest(results, report, {"name": source.uri, "blueprint_hash": source.blueprint_hash}, dry_dir)
            print(f"dry-run artifacts written to {dry_dir}/")
        else:
            n_entities = sum(len(r.entities) for r in results)
            n_relations = sum(len(r.relations) for r in results)
            n_unmapped = sum(len(r.unmapped_mentions) for r in results)
            print(f"extracted {n_entities} entities, {n_relations} relations, {n_unmapped} unmapped mentions")

        s = report.summary
        print(f"cost: ${s['total_cost_usd']:.4f} ({s['cache_savings_pct']}% saved by prompt caching)")


def cmd_cook(args: argparse.Namespace) -> None:
    """Run the full pipeline: resolve, embed, write, validate."""
    from forge.pipeline import PipelineResult, run_pipeline, run_pipeline_report

    store_path = args.store_path or Path.cwd() / "forge.db"

    # Load source
    with RegistryStore(store_path) as store:
        source = store.get_source(args.source_id)
        if source is None:
            sys.exit(f"source not found: {args.source_id}")

        chunks = store.get_chunks_for_source(args.source_id)
        if not chunks:
            sys.exit(f"no chunks found for source: {args.source_id}")

        # Load blueprint info from source metadata
        bp = {"name": source.uri, "blueprint_hash": source.blueprint_hash}

        # Reconstruct extraction results from stored data
        # (in production these come from the extraction step)
        from forge.extract.models import ExtractedEntity, ExtractionResult
        from forge.extract.provider import extractor_for

        extractor = extractor_for("mock")
        # Build a minimal blueprint with ontology for mock extraction
        if hasattr(store, "entities"):
            _ = store.entities  # ensure loaded

        chunks_data = [
            {"chunk_id": c.chunk_id, "text": c.text, "path": c.path}
            for c in chunks
        ]

    # Run the pipeline
    store_kwargs = {"backend": "memory"}
    if args.store == "neo4j":
        store_kwargs = {
            "uri": args.neo4j_uri or "bolt://localhost:7687",
            "username": args.neo4j_user or "neo4j",
            "password": args.neo4j_password or "",
        }

    # We need the actual blueprint for ontology resolution
    # For now, try to load it from the source metadata path
    blueprint_path = Path.cwd() / "blueprint.yaml"
    if args.blueprint:
        blueprint_path = Path(args.blueprint)

    try:
        bp = load_blueprint(blueprint_path)
    except (BlueprintError, BlueprintNotFoundError):
        print("WARNING  no blueprint found for resolution — using pass-through", file=sys.stderr)
        bp = {
            "name": source.uri,
            "blueprint_hash": source.blueprint_hash,
            "blueprint_version": source.blueprint_version,
            "ontology": {"closed": False, "node_types": [], "relationship_types": [], "patterns": []},
            "embedding": {"targets": []},
            "structure": {},
        }

    # Extract (using mock for now, no API key)
    from forge.extract import run_extraction as _run_extraction

    extraction_results, cost_report = _run_extraction(
        blueprint=bp,
        chunks=chunks_data,
        source_id=source.source_id,
        provider=args.provider or "mock",
        dry_run=args.dry_run,
        dry_run_dir=args.dry_run_dir or "forge-dry-run",
    )

    result = run_pipeline(
        blueprint=bp,
        source=source,
        chunks=chunks,
        extraction_results=extraction_results,
        store_backend=args.store,
        embed_backend=args.embed_backend,
        store_kwargs=store_kwargs if args.store == "neo4j" else {},
        embed_kwargs={"dimensions": 384},
        hub_threshold=args.hub_threshold or 50,
        dry_run=args.dry_run,
    )

    print(run_pipeline_report(result))

    if result.drift_report and result.drift_report.thresholds_exceeded:
        print("\n⚠ Drift thresholds exceeded — re-opening the blueprint is recommended.")


def cmd_update(args: argparse.Namespace) -> None:
    """Re-process a source — re-chunk, re-extract, resolve only changes."""
    from forge.pipeline import PipelineResult, run_pipeline_report, run_update_pipeline

    store_path = args.store_path or Path.cwd() / "forge.db"

    with RegistryStore(store_path) as store:
        source = store.get_source(args.source_id)
        if source is None:
            sys.exit(f"source not found: {args.source_id}")

        old_chunks = store.get_chunks_for_source(args.source_id)
        if not old_chunks:
            sys.exit(f"no chunks found for source: {args.source_id}. Use 'forge add' instead.")

        # Load blueprint
        blueprint_path = Path(args.blueprint) if args.blueprint else Path.cwd() / "blueprint.yaml"
        try:
            bp = load_blueprint(blueprint_path)
        except (BlueprintError, BlueprintNotFoundError):
            bp = {"name": source.uri, "blueprint_hash": source.blueprint_hash,
                  "blueprint_version": source.blueprint_version,
                  "ontology": {}, "embedding": {"targets": []}, "structure": {}}

        # Re-load source text and re-chunk
        from forge.chunking import chunk_document
        from forge.loaders import load_source

        path = Path(source.uri)
        if not path.exists():
            sys.exit(f"source file not found: {path}")

        try:
            mime, text = load_source(path)
        except ValueError as e:
            sys.exit(str(e))

        new_chunks = chunk_document(text, bp.get("structure", {}), source.source_id, bp.get("blueprint_hash", ""))

        # Re-extract
        chunks_data = [{"chunk_id": c.chunk_id, "text": c.text, "path": c.path} for c in new_chunks]
        from forge.extract import run_extraction as _run_extraction
        new_results, cost_report = _run_extraction(
            blueprint=bp, chunks=chunks_data, source_id=source.source_id,
            provider=args.provider or "mock", dry_run=args.dry_run,
            dry_run_dir=args.dry_run_dir or "forge-dry-run",
        )

        # Build old extraction results from stored chunks (mock)
        from forge.extract.models import ExtractionResult
        old_results = [
            ExtractionResult(chunk_id=c.chunk_id, source_id=source.source_id)
            for c in old_chunks
        ]

        result = run_update_pipeline(
            blueprint=bp, source=source, old_extraction_results=old_results,
            new_chunks=new_chunks, new_extraction_results=new_results,
            store_backend=args.store, embed_backend=args.embed_backend,
            hub_threshold=args.hub_threshold or 50, dry_run=args.dry_run,
        )

        # Update source content_hash in registry
        if hasattr(store, "update_source_status"):
            store.update_source_status(source.source_id, "ready")

        print(run_pipeline_report(result))
        if result.drift_report and result.drift_report.thresholds_exceeded:
            print("\n\u26a0 Drift thresholds exceeded.")


def cmd_conflicts(args: argparse.Namespace) -> None:
    """List assertion conflicts across sources."""
    from forge.conflict import find_conflicts, format_conflicts

    store_path = args.store_path or Path.cwd() / "forge.db"

    try:
        from forge.store import store_for
        store = store_for("memory")
        bp = load_blueprint(args.blueprint) if args.blueprint else {}
    except Exception:
        sys.exit("need a populated store for conflict detection")

    # Load data from forge.db if available
    forge_db = Path(store_path)
    if forge_db.exists():
        try:
            from forge.registry.store import RegistryStore
            reg = RegistryStore(forge_db)
            for s in reg.list_sources():
                if s.status == "retracted":
                    continue
                chks = reg.get_chunks_for_source(s.source_id)
                for c in chks:
                    store.write_chunk(c, s)
            reg.close()
        except Exception as e:
            print(f"WARNING  could not load forge.db: {e}", file=sys.stderr)

    relations = store.get_relations()
    source_uris = store.get_all_source_uris()
    conflicts = find_conflicts(relations, source_uris)
    print(format_conflicts(conflicts))


def cmd_blueprint(args: argparse.Namespace) -> None:
    """Validate a blueprint (check only, no source added)."""
    try:
        bp = load_blueprint(args.blueprint, strict=args.strict)
        print(f"valid: {bp['name']} v{bp['blueprint_version']} hash={bp['blueprint_hash'][:20]}")
    except BlueprintError as e:
        for err in e.errors:
            print(f"ERROR  {err}", file=sys.stderr)
        for warn in e.warnings:
            print(f"warn   {warn}", file=sys.stderr)
        sys.exit(f"invalid ({len(e.errors)} error(s), {len(e.warnings)} warning(s))")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Forge — source registry and chunking")
    ap.add_argument("--store", dest="store_path", help="Path to SQLite registry (default: ./forge.db)")

    sub = ap.add_subparsers(dest="command", required=True)

    # forge add
    add_p = sub.add_parser("add", help="Register and chunk a source")
    add_p.add_argument("path", help="Path to source file")
    add_p.add_argument("--blueprint", "-b", required=True, help="Blueprint YAML")
    add_p.add_argument("--owner", default="", help="Source owner identifier")
    add_p.add_argument("--visibility", choices=["public", "private"], default="public")
    add_p.add_argument("--dry-run", action="store_true",
                       help="Skip LLM; write prompts to disk")
    add_p.add_argument("--provider", choices=["mock", "openai"], default="mock",
                       help="Extraction provider (default: mock)")

    # forge list
    list_p = sub.add_parser("list", help="List registered sources")
    list_p.add_argument("--status", help="Filter by status (pending, chunking, ready, retracted)")

    # forge status
    status_p = sub.add_parser("status", help="Show source detail")
    status_p.add_argument("source_id")

    # forge retract
    retract_p = sub.add_parser("retract", help="Retract a source")
    retract_p.add_argument("source_id")

    # forge chunks
    chunks_p = sub.add_parser("chunks", help="List chunks for a source")
    chunks_p.add_argument("source_id")
    chunks_p.add_argument("--json", action="store_true", help="JSON output")

    # forge extract
    extract_p = sub.add_parser("extract", help="Run extraction on a registered source")
    extract_p.add_argument("source_id")
    extract_p.add_argument("--provider", choices=["mock", "openai"], default="mock",
                           help="Extraction provider (default: mock, no LLM)")
    extract_p.add_argument("--dry-run", action="store_true",
                           help="Write prompts to disk without calling LLM")
    extract_p.add_argument("--dry-run-dir", default="forge-dry-run",
                           help="Output directory for dry-run artifacts")

    # forge cook
    cook_p = sub.add_parser("cook", help="Run resolve -> embed -> write -> validate pipeline")
    cook_p.add_argument("source_id", help="Source identifier")
    cook_p.add_argument("--blueprint", "-b", help="Blueprint YAML path (auto-detect if omitted)")
    cook_p.add_argument("--store", choices=["memory", "neo4j"], default="memory",
                        help="Storage backend (default: memory)")
    cook_p.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    cook_p.add_argument("--neo4j-user", default="neo4j")
    cook_p.add_argument("--neo4j-password", default="")
    cook_p.add_argument("--embed-backend", choices=["mock", "local"], default="mock",
                        help="Embedding backend (default: mock, no local model)")
    cook_p.add_argument("--provider", choices=["mock", "openai"], default="mock",
                        help="Extraction provider")
    cook_p.add_argument("--dry-run", action="store_true",
                        help="Skip writes, output prompts to disk")
    cook_p.add_argument("--dry-run-dir", default="forge-dry-run",
                        help="Output directory for dry-run artifacts")
    cook_p.add_argument("--hub-threshold", type=int, default=50,
                        help="Degree threshold for hub detection")

    # forge update
    update_p = sub.add_parser("update", help="Re-process a source incrementally")
    update_p.add_argument("source_id", help="Source identifier")
    update_p.add_argument("--blueprint", "-b", help="Blueprint YAML path")
    update_p.add_argument("--store", choices=["memory", "neo4j"], default="memory")
    update_p.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    update_p.add_argument("--neo4j-user", default="neo4j")
    update_p.add_argument("--neo4j-password", default="")
    update_p.add_argument("--embed-backend", choices=["mock", "local"], default="mock")
    update_p.add_argument("--provider", choices=["mock", "openai"], default="mock")
    update_p.add_argument("--dry-run", action="store_true")
    update_p.add_argument("--dry-run-dir", default="forge-dry-run")
    update_p.add_argument("--hub-threshold", type=int, default=50)

    # forge conflicts
    conflicts_p = sub.add_parser("conflicts", help="List assertion conflicts between sources")
    conflicts_p.add_argument("--blueprint", "-b", help="Blueprint YAML (for ontology context)")

    # forge blueprint
    bp_p = sub.add_parser("blueprint", help="Validate a blueprint without adding a source")
    bp_p.add_argument("blueprint", help="Blueprint YAML path")
    bp_p.add_argument("--strict", action="store_true", help="Treat warnings as errors")

    args = ap.parse_args()
    commands = {
        "add": cmd_add,
        "list": cmd_list,
        "status": cmd_status,
        "retract": cmd_retract,
        "chunks": cmd_chunks,
        "extract": cmd_extract,
        "cook": cmd_cook,
        "update": cmd_update,
        "conflicts": cmd_conflicts,
        "blueprint": cmd_blueprint,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()