#!/usr/bin/env python3
"""Score retrieval against a golden set: the graph versus plain vector RAG.

    python eval/run_eval.py --blueprint spec/examples/tenancy.blueprint.yaml \\
        --source spec/examples/corpora/tenancy-act.md

Builds the graph in memory with Forge (same loader, chunker, extractor and
pipeline as `forge add` + `forge cook`), then runs every golden-set question
through Serve twice: the hybrid retriever (vector + fulltext + graph), and
vector similarity alone. The blueprint's workload exists to make the ontology
falsifiable; this is where it gets falsified. If hybrid does not beat vector
on the multi_hop shapes, more graph will not fix it, the schema is wrong.

Retrieval is scored separately from generation, per golden item:

  gold_paths   chunk paths that should be retrieved ("Deposits / Maximum deposit")
  gold_chunks  chunk id prefixes. Ids include the source id, so they only hold
               for one build; prefer gold_paths
  gold_nodes   "Label:name" entities whose chunks should be retrieved. A node
               the build never produced is reported as not_in_graph, so an
               extraction miss is not mistaken for a retrieval miss
  must_include / must_not_include
               strings that must (not) appear in the retrieved text

Runs offline by default (mock extractor, mock embedder), which proves the
harness and the plumbing, not quality: mock vectors are hashes. For real
numbers use --embed-backend local, and --provider openai, which spends money.

Exit codes: 0 ok, 1 build errors or hybrid recall below --min-recall.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Prefer this checkout over any installed copy of forge or serve, so the
# harness measures the code it sits next to.
sys.path[:0] = [str(ROOT / "forge" / "src"), str(ROOT / "serve" / "src")]

from forge.blueprint import BlueprintError, load_blueprint  # noqa: E402
from forge.chunking import chunk_document  # noqa: E402
from forge.embed import embed_for  # noqa: E402
from forge.extract import run_extraction  # noqa: E402
from forge.loaders import load_source  # noqa: E402
from forge.pipeline import _embed_kwargs, run_pipeline  # noqa: E402
from forge.registry.models import Source  # noqa: E402
from forge.store import MemoryStore  # noqa: E402
from serve.retrievers.hybrid import HybridRetriever  # noqa: E402

MODES = ("hybrid", "vector")


# ------------------------------------------------------------------ build

def build_graph(bp: dict, sources: list[Path], provider: str, embed_backend: str) -> tuple[MemoryStore, list[str]]:
    store = MemoryStore()
    errors: list[str] = []
    for path in sources:
        mime, text = load_source(path)
        source = Source(
            uri=str(path),
            content_hash="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            mime=mime,
            blueprint_version=bp["blueprint_version"],
            blueprint_hash=bp["blueprint_hash"],
            source_id=str(uuid.uuid4()),
        )
        chunks = chunk_document(text, bp["structure"], source.source_id, bp["blueprint_hash"])
        chunks_data = [{"chunk_id": c.chunk_id, "text": c.text, "path": c.path} for c in chunks]
        results, _ = run_extraction(bp, chunks_data, source.source_id, provider=provider)
        res = run_pipeline(bp, source, chunks, results, embed_backend=embed_backend, store=store)
        errors.extend(f"{path.name}: {e}" for e in res.errors)
        print(
            f"built {path.name}: {res.chunks_written} chunks, {res.entities_written} entities, "
            f"{res.relations_written} relations, {res.embeddings_written} embeddings",
            file=sys.stderr,
        )
    return store, errors


# ---------------------------------------------------------------- scoring

@dataclass
class ModeScore:
    golds: int = 0
    found: int = 0
    not_in_graph: int = 0
    first_rank: int | None = None  # 1-based rank of the first gold hit
    include_total: int = 0
    include_found: int = 0
    forbidden_seen: list[str] = field(default_factory=list)
    retrieved_paths: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        return self.found / self.golds if self.golds else None

    @property
    def hit(self) -> bool | None:
        return self.found > 0 if self.golds else None


def _chunk_nodes(store: MemoryStore) -> dict[str, set[str]]:
    """chunk_id -> {"label:name"}, lowercased for matching."""
    out: dict[str, set[str]] = {}
    for eid, chunk_ids in store.entity_chunks.items():
        ent = store.entities.get(eid)
        if not ent:
            continue
        key = f"{ent['label']}:{ent['name']}".lower()
        for cid in chunk_ids:
            out.setdefault(cid, set()).add(key)
    return out


def score_item(item: dict, hit_ids: list[str], store: MemoryStore, chunk_nodes: dict[str, set[str]]) -> ModeScore:
    s = ModeScore()
    chunks = [store.chunks[cid] for cid in hit_ids if cid in store.chunks]
    s.retrieved_paths = [c.path for c in chunks]
    all_nodes = set().union(*chunk_nodes.values()) if chunk_nodes else set()

    # Each gold is a predicate over a retrieved chunk.
    golds: list[tuple[str, object]] = []
    golds += [("path", p) for p in item.get("gold_paths", [])]
    golds += [("chunk", c) for c in item.get("gold_chunks", [])]
    for n in item.get("gold_nodes", []):
        if n.lower() in all_nodes:
            golds.append(("node", n.lower()))
        else:
            s.not_in_graph += 1
    s.golds = len(golds) + s.not_in_graph

    for kind, value in golds:
        for rank, c in enumerate(chunks, start=1):
            if (
                (kind == "path" and c.path == value)
                or (kind == "chunk" and c.chunk_id.startswith(str(value)))
                or (kind == "node" and value in chunk_nodes.get(c.chunk_id, set()))
            ):
                s.found += 1
                s.first_rank = rank if s.first_rank is None else min(s.first_rank, rank)
                break

    # Full chunk text, not the hit's 500-char preview.
    text = "\n".join(c.text for c in chunks).lower()
    must = item.get("must_include", [])
    s.include_total = len(must)
    s.include_found = sum(m.lower() in text for m in must)
    s.forbidden_seen = [m for m in item.get("must_not_include", []) if m.lower() in text]
    return s


def aggregate(scores: list[ModeScore]) -> dict:
    scored = [s for s in scores if s.golds]
    n = len(scored)
    return {
        "items": len(scores),
        "scored": n,
        "hit_at_k": sum(bool(s.hit) for s in scored) / n if n else None,
        "recall_at_k": sum(s.recall for s in scored) / n if n else None,
        "mrr": sum(1 / s.first_rank for s in scored if s.first_rank) / n if n else None,
        "not_in_graph": sum(s.not_in_graph for s in scores),
        "must_include": (
            sum(s.include_found for s in scores) / t
            if (t := sum(s.include_total for s in scores)) else None
        ),
        "must_not_violations": sum(bool(s.forbidden_seen) for s in scores),
    }


# ----------------------------------------------------------------- report

def _fmt(v: float | None) -> str:
    return "  -  " if v is None else f"{v:5.2f}"


def print_report(summary: dict, top_k: int) -> None:
    print(f"\nrecall@{top_k} by shape (hybrid vs vector baseline)\n")
    print(f"  {'shape':<14}{'n':>3}   {'hit@k':>13}   {'recall@k':>13}   {'mrr':>13}   {'must_incl':>13}   not_in_graph")
    for shape, modes in summary["by_shape"].items():
        h, v = modes["hybrid"], modes["vector"]
        print(
            f"  {shape:<14}{h['items']:>3}   "
            f"{_fmt(h['hit_at_k'])} {_fmt(v['hit_at_k'])}   "
            f"{_fmt(h['recall_at_k'])} {_fmt(v['recall_at_k'])}   "
            f"{_fmt(h['mrr'])} {_fmt(v['mrr'])}   "
            f"{_fmt(h['must_include'])} {_fmt(v['must_include'])}   "
            f"{h['not_in_graph']}"
        )
    h, v = summary["overall"]["hybrid"], summary["overall"]["vector"]
    print(f"\n  overall recall@{top_k}: hybrid {_fmt(h['recall_at_k']).strip()}, vector {_fmt(v['recall_at_k']).strip()}")
    for mode in MODES:
        if summary["overall"][mode]["must_not_violations"]:
            print(f"  {mode}: {summary['overall'][mode]['must_not_violations']} item(s) retrieved forbidden text")
    if summary["overall"]["hybrid"]["not_in_graph"]:
        print("  not_in_graph counts gold nodes the build never produced: an extraction miss, not a retrieval miss.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--blueprint", required=True)
    ap.add_argument("--source", required=True, nargs="+", type=Path, help="source file(s) to build the graph from")
    ap.add_argument("--golden", help="golden set JSONL (default: the blueprint's workload.golden_set)")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--hops", type=int, default=1, help="graph expansion hops for the hybrid retriever")
    ap.add_argument("--provider", choices=["mock", "openai"], default="mock",
                    help="extraction provider; openai calls the API and costs money")
    ap.add_argument("--embed-backend", choices=["mock", "local"], default="mock")
    ap.add_argument("--json", type=Path, help="write the full per-item report here")
    ap.add_argument("--min-recall", type=float, help="exit 1 if overall hybrid recall@k is below this")
    ap.add_argument("--verbose", "-v", action="store_true", help="print every item's retrieved paths")
    args = ap.parse_args()

    try:
        bp = load_blueprint(args.blueprint)
    except BlueprintError as e:
        for msg in e.errors:
            print(f"ERROR  {msg}", file=sys.stderr)
        sys.exit("blueprint is invalid")

    golden = Path(args.golden or bp.get("workload", {}).get("golden_set") or "")
    if not golden.is_file():
        sys.exit(f"golden set not found: {golden or '(blueprint declares none)'}")
    items = [json.loads(line) for line in golden.read_text(encoding="utf-8").splitlines() if line.strip()]
    for p in args.source:
        if not p.is_file():
            sys.exit(f"source not found: {p}")

    store, errors = build_graph(bp, args.source, args.provider, args.embed_backend)
    if args.embed_backend == "mock":
        print("embed backend is mock: vector scores test the plumbing, not quality", file=sys.stderr)

    embedder = embed_for(args.embed_backend, **_embed_kwargs(bp, args.embed_backend, None))
    retriever = HybridRetriever(store, embed_query=lambda q: embedder.embed(q))
    chunk_nodes = _chunk_nodes(store)

    per_item: list[dict] = []
    by_mode: dict[str, list[ModeScore]] = {m: [] for m in MODES}
    by_shape: dict[str, dict[str, list[ModeScore]]] = {}
    for item in items:
        hybrid = retriever.retrieve(item["q"], top_k=args.top_k, hops=args.hops)
        vector = retriever.retrieve_vector(embedder.embed(item["q"]), top_k=args.top_k)
        row = {"id": item.get("id"), "shape": item.get("shape", "unknown"), "q": item["q"]}
        for mode, result in (("hybrid", hybrid), ("vector", vector)):
            s = score_item(item, [h.chunk_id for h in result.hits], store, chunk_nodes)
            by_mode[mode].append(s)
            by_shape.setdefault(row["shape"], {m: [] for m in MODES})[mode].append(s)
            row[mode] = {**asdict(s), "recall": s.recall}
        per_item.append(row)
        if args.verbose:
            print(f"\n[{row['id']}] {row['shape']}: {row['q']}")
            for mode in MODES:
                r = row[mode]
                print(f"  {mode:<7} found {r['found']}/{r['golds']}  {r['retrieved_paths']}")

    summary = {
        "blueprint": bp["name"],
        "blueprint_hash": bp["blueprint_hash"],
        "golden_set": str(golden),
        "top_k": args.top_k,
        "provider": args.provider,
        "embed_backend": args.embed_backend,
        "build_errors": errors,
        "overall": {m: aggregate(by_mode[m]) for m in MODES},
        "by_shape": {sh: {m: aggregate(v[m]) for m in MODES} for sh, v in by_shape.items()},
        "items": per_item,
    }
    print_report(summary, args.top_k)

    if args.json:
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nreport: {args.json}")

    failed = False
    for e in errors:
        print(f"ERROR  build: {e}", file=sys.stderr)
        failed = True
    recall = summary["overall"]["hybrid"]["recall_at_k"]
    if args.min_recall is not None and (recall is None or recall < args.min_recall):
        shown = "n/a" if recall is None else f"{recall:.2f}"
        print(f"FAIL   hybrid recall@{args.top_k} {shown} is below --min-recall {args.min_recall}", file=sys.stderr)
        failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
