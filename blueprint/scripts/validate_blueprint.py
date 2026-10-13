#!/usr/bin/env python3
"""Validate a blueprint against the spec, then against the rules JSON Schema
cannot express.

    python scripts/validate_blueprint.py blueprints/firecraft.yaml
    python scripts/validate_blueprint.py blueprints/firecraft.yaml --hash

Forge and Serve both run this before doing anything. A blueprint that does not
validate is not a blueprint.

Exit codes: 0 clean, 1 errors, 2 warnings only (with --strict, 1).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

SPEC = Path(__file__).resolve().parent.parent.parent / "spec" / "blueprint.schema.json"

ERRORS: list[str] = []
WARNINGS: list[str] = []


def err(msg: str) -> None:
    ERRORS.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def labels_of(items) -> set[str]:
    return {i["label"] for i in items}


# ---------------------------------------------------------------- structural

def check_schema(bp: dict) -> None:
    try:
        import jsonschema
    except ImportError:
        warn("jsonschema not installed; skipped spec validation (pip install jsonschema)")
        return
    schema = json.loads(SPEC.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for e in sorted(validator.iter_errors(bp), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        err(f"spec: {loc}: {e.message}")


# ------------------------------------------------------------- cross-section

def check_structure(bp: dict) -> None:
    st = bp.get("structure", {})
    levels = [h["level"] for h in st.get("hierarchy", [])]
    if st.get("chunk_at") and st["chunk_at"] not in levels:
        err(f"structure.chunk_at '{st['chunk_at']}' is not a declared hierarchy level {levels}")

    for h in st.get("hierarchy", []):
        try:
            rx = re.compile(h["pattern"], re.MULTILINE)
        except re.error as e:
            err(f"structure.hierarchy[{h['level']}]: invalid regex: {e}")
            continue
        if rx.groups == 0:
            err(f"structure.hierarchy[{h['level']}]: pattern has no capture group")
        if rx.groups > 2:
            warn(
                f"structure.hierarchy[{h['level']}]: {rx.groups} capture groups; "
                "only the first two are used (number, title)"
            )
        if not h.get("example"):
            warn(
                f"structure.hierarchy[{h['level']}]: no example line. "
                "An untested pattern is a guess, and a guess here produces one chunk per document."
            )
        elif not rx.search(h["example"]):
            err(f"structure.hierarchy[{h['level']}]: pattern does not match its own example")

    for x in st.get("xref_patterns", []):
        try:
            re.compile(x["pattern"])
        except re.error as e:
            err(f"structure.xref_patterns[{x['name']}]: invalid regex: {e}")
        if x.get("target_level") and x["target_level"] not in levels:
            warn(
                f"structure.xref_patterns[{x['name']}]: target_level "
                f"'{x['target_level']}' is not a hierarchy level; refs will not resolve"
            )

    for s in st.get("skip_regions", []):
        try:
            re.compile(s["match"], re.MULTILINE)
        except re.error as e:
            err(f"structure.skip_regions: invalid regex: {e}")

    if "pdf" in st.get("source_types", []) and not st.get("skip_regions"):
        warn(
            "source_types includes pdf but no skip_regions declared. Books have a "
            "table of contents and an index that match heading patterns and produce "
            "chunks you pay to extract nothing from."
        )


def check_ontology(bp: dict) -> None:
    on = bp.get("ontology", {})
    nodes = labels_of(on.get("node_types", []))
    rels = labels_of(on.get("relationship_types", []))
    det = set(on.get("deterministic_relations", []))

    used_rels: set[str] = set()
    for i, p in enumerate(on.get("patterns", [])):
        if len(p) != 3:
            continue
        src, rel, dst = p
        if src not in nodes:
            err(f"ontology.patterns[{i}]: source '{src}' is not a declared node type")
        if dst not in nodes:
            err(f"ontology.patterns[{i}]: target '{dst}' is not a declared node type")
        if rel not in rels:
            err(f"ontology.patterns[{i}]: relation '{rel}' is not a declared relationship type")
        used_rels.add(rel)

    for r in rels - used_rels:
        err(
            f"ontology: relationship '{r}' has no pattern. A relationship with no "
            "declared domain/range is unconstrained, which defeats the main validation."
        )

    # Every node type should be reachable, or it will never be populated.
    in_patterns = {p[0] for p in on.get("patterns", []) if len(p) == 3}
    in_patterns |= {p[2] for p in on.get("patterns", []) if len(p) == 3}
    for n in nodes - in_patterns:
        warn(f"ontology: node type '{n}' appears in no pattern; nothing will ever connect to it")

    for r in det - rels:
        err(f"ontology.deterministic_relations: '{r}' is not a declared relationship type")

    xref_rels = {x["relation"] for x in bp.get("structure", {}).get("xref_patterns", [])}
    for r in xref_rels - det:
        warn(
            f"'{r}' is parsed by structure.xref_patterns but is not in "
            "ontology.deterministic_relations. The extractor will be asked for it too, "
            "producing lower-precision duplicates of edges you already trust."
        )

    for label in on.get("vocabularies", {}):
        if label not in nodes:
            err(f"ontology.vocabularies: '{label}' is not a declared node type")
    for rule in on.get("resolution", []):
        for label in rule["labels"]:
            if label not in nodes:
                err(f"ontology.resolution: '{label}' is not a declared node type")

    n = len(nodes)
    if n > 15:
        warn(
            f"{n} node types. Past about 15 the extractor is choosing between "
            "near-synonymous labels and the choice becomes arbitrary. Check whether "
            "some are properties."
        )
    if bp.get("ontology", {}).get("temporal", {}).get("enabled"):
        for label in on["temporal"].get("labels", []):
            if label not in nodes:
                err(f"ontology.temporal.labels: '{label}' is not a declared node type")

    for nt in on.get("node_types", []):
        if not nt.get("answers"):
            warn(
                f"node type '{nt['label']}' does not name the question it answers. "
                "A type that cannot name one is usually a property."
            )


KNOWN_DIMS = {
    "all-minilm-l6-v2": 384,
    "all-mpnet-base-v2": 768,
    "bge-small-en-v1.5": 384,
    "bge-base-en-v1.5": 768,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "embed-english-v3.0": 1024,
    "mistral-embed": 1024,
}


def check_embedding(bp: dict) -> None:
    em = bp.get("embedding", {})
    nodes = labels_of(bp.get("ontology", {}).get("node_types", []))
    model = em.get("model", "").split("/")[-1].lower()
    expected = KNOWN_DIMS.get(model)
    if expected and em.get("dimensions") != expected:
        err(
            f"embedding: {em['model']} produces {expected}-d vectors but "
            f"dimensions is {em['dimensions']}. A mismatch does not raise at "
            "runtime; it makes every search return nothing."
        )

    facets = []
    for t in em.get("targets", []):
        of = t["of"] if isinstance(t["of"], list) else [t["of"]]
        for label in of:
            if label not in nodes and label != "Chunk":
                err(f"embedding.targets[{t['name']}]: '{label}' is not a declared node type")
        fields = set(re.findall(r"\{(\w+)\}", t.get("text", "")))
        if not fields:
            warn(f"embedding.targets[{t['name']}]: text template has no fields")
        if t["of"] == "Chunk" and "path" not in fields:
            warn(
                f"embedding.targets[{t['name']}]: chunk text without {{path}}. "
                "The hierarchy breadcrumb is what disambiguates a chunk; 'Article 12' "
                "alone means nothing."
            )
        if t.get("facet"):
            facets.append(t["facet"])

    if em.get("multi_vector") and len(set(facets)) < 2:
        err("embedding.multi_vector is true but fewer than two distinct facets are declared")
    if facets and not em.get("multi_vector"):
        warn("embedding targets declare facets but multi_vector is false; facets will be ignored")


def check_workload(bp: dict) -> None:
    wl = bp.get("workload", {})
    rels = labels_of(bp.get("ontology", {}).get("relationship_types", []))
    nodes = labels_of(bp.get("ontology", {}).get("node_types", []))
    kinds = {s["kind"] for s in wl.get("shapes", [])}

    if "lookup" not in kinds:
        warn(
            "no 'lookup' shape declared. Include one: plain vector RAG should match "
            "or beat the graph there, and if the graph is worse the retrieval query "
            "is over-expanding."
        )
    if "multi_hop" not in kinds:
        err(
            "no 'multi_hop' shape declared. That is the shape a graph exists to serve. "
            "If none of the real questions need it, a vector store is the cheaper answer."
        )

    for s in wl.get("shapes", []):
        for r in s.get("must_traverse", []):
            if r not in rels:
                err(f"workload.shapes[{s['id']}].must_traverse: '{r}' is not a declared relationship")
        for n in s.get("must_return", []):
            if n not in nodes:
                err(f"workload.shapes[{s['id']}].must_return: '{n}' is not a declared node type")
        if s["kind"] == "multi_hop" and s.get("hops", 0) < 1:
            warn(f"workload.shapes[{s['id']}]: multi_hop with hops < 1")

    gs = wl.get("golden_set")
    if not gs:
        warn(
            "no golden_set. Without labelled questions there is no way to tell whether "
            "a later change to the ontology or retrieval helped or hurt."
        )
    else:
        p = Path(gs)
        if not p.is_absolute():
            p = Path.cwd() / gs
        if not p.exists():
            warn(f"workload.golden_set '{gs}' does not exist yet")
        else:
            n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
            if n < 20:
                warn(f"golden_set has {n} items; 30 to 50 is the useful range")


def check_guardrails(bp: dict) -> None:
    gr = bp.get("guardrails", {})
    nodes = labels_of(bp.get("ontology", {}).get("node_types", []))
    for f in gr.get("hard_filters", []):
        if f["mode"] in ("exclude", "restrict") and not f.get("cypher"):
            err(f"guardrails.hard_filters[{f['name']}]: mode '{f['mode']}' needs a cypher clause")
        if f["mode"] == "force_include" and not f.get("labels"):
            err(f"guardrails.hard_filters[{f['name']}]: force_include needs labels")
        for label in f.get("labels", []):
            if label not in nodes:
                err(f"guardrails.hard_filters[{f['name']}]: '{label}' is not a declared node type")
        declared = set(f.get("params", []))
        used = set(re.findall(r"\$(\w+)", f.get("cypher", "")))
        for p in used - declared - {"user_id"}:
            warn(f"guardrails.hard_filters[{f['name']}]: uses ${p} but does not declare it in params")
        if not f.get("rationale"):
            warn(f"guardrails.hard_filters[{f['name']}]: no rationale recorded")

    if gr.get("hard_filters") and not gr.get("release_blockers"):
        warn(
            "hard filters declared but no release_blockers. A safety filter with no "
            "automated leak suite is an intention, not a guarantee."
        )


def check_drift(bp: dict) -> None:
    if not bp.get("drift"):
        warn(
            "no drift thresholds. Forge sees the long tail of real sources that design "
            "time never will; without thresholds that signal is discarded."
        )


# ------------------------------------------------------------ forge contract
#
# The spec describes what a blueprint may say. These rules describe what Forge
# can currently execute. A blueprint that passes the spec but not these fails
# at build time instead, unattended, after money has been spent. Keep this
# section in step with Forge: when Forge gains a loader or a template field,
# relax the rule here in the same change.

FORGE_LOADERS = {"pdf", "markdown", "text", "html"}
FORGE_EMBED_PROVIDERS = {"local"}
FORGE_RESOLUTION_TIERS = {"exact", "fuzzy"}
# Fields Forge supplies when it renders an embedding template. format() raises
# on anything else, and that takes down the whole embedding step for a source.
CHUNK_TEMPLATE_FIELDS = {"path", "text"}
ENTITY_TEMPLATE_FIELDS = {"name", "label"}


def check_forge_contract(bp: dict) -> None:
    st = bp.get("structure", {})
    for t in st.get("source_types", []):
        if t not in FORGE_LOADERS:
            err(
                f"structure.source_types: Forge has no loader for '{t}' "
                f"(it loads {', '.join(sorted(FORGE_LOADERS))}). Convert the sources first."
            )

    em = bp.get("embedding", {})
    provider = em.get("provider", "local")
    if provider not in FORGE_EMBED_PROVIDERS:
        err(
            f"embedding.provider: Forge has no '{provider}' embedder "
            f"(it has {', '.join(sorted(FORGE_EMBED_PROVIDERS))})."
        )

    for t in em.get("targets", []):
        of = t["of"] if isinstance(t["of"], list) else [t["of"]]
        fields = set(re.findall(r"\{(\w+)\}", t.get("text", "")))
        if "Chunk" in of:
            for f in sorted(fields - CHUNK_TEMPLATE_FIELDS):
                err(
                    f"embedding.targets[{t['name']}]: chunk template uses {{{f}}}; "
                    f"Forge only supplies {{path}} and {{text}} to chunk templates"
                )
        if any(label != "Chunk" for label in of):
            for f in sorted(fields - ENTITY_TEMPLATE_FIELDS):
                err(
                    f"embedding.targets[{t['name']}]: entity template uses {{{f}}}; "
                    f"Forge only supplies {{name}} and {{label}} to entity templates"
                )

    for label, v in bp.get("ontology", {}).get("vocabularies", {}).items():
        unbuilt = [t for t in v.get("tiers", []) if t not in FORGE_RESOLUTION_TIERS]
        if unbuilt:
            warn(
                f"ontology.vocabularies[{label}]: Forge does not implement tiers "
                f"{unbuilt} yet. Mentions that miss exact and fuzzy become new "
                "entities instead of anchoring to the vocabulary."
            )
        p = Path(v["source"])
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists():
            warn(
                f"ontology.vocabularies[{label}]: '{v['source']}' does not exist. "
                "Resolution for this type passes through unanchored until it does."
            )
            continue
        header = p.read_text(encoding="utf-8").splitlines()[:1]
        cols = [c.strip() for c in header[0].split(",")] if header else []
        if "canonical" not in cols:
            err(
                f"ontology.vocabularies[{label}]: '{v['source']}' has no 'canonical' "
                "column. Forge reads a CSV with header canonical,aliases "
                "(aliases separated by |)."
            )

    if st.get("xref_patterns"):
        warn(
            "structure.xref_patterns: Forge does not parse cross-references yet. "
            "Deterministic relations will be declared and excluded from extraction "
            "but never written, so shapes that traverse them will come back empty."
        )


CHECKS = (
    check_schema,
    check_structure,
    check_ontology,
    check_embedding,
    check_workload,
    check_guardrails,
    check_drift,
    check_forge_contract,
)


def run_checks(bp: dict) -> tuple[list[str], list[str]]:
    """Run every check and return (errors, warnings). Forge calls this too, so
    adding a check here is enough for both sides to enforce it."""
    ERRORS.clear()
    WARNINGS.clear()
    for check in CHECKS:
        check(bp)
    return list(ERRORS), list(WARNINGS)


# -------------------------------------------------------------------- hashing

def compute_hash(bp: dict) -> str:
    body = {k: v for k, v in bp.items() if k != "blueprint_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("blueprint")
    ap.add_argument("--hash", action="store_true", help="write blueprint_hash into the file")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failure")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    path = Path(args.blueprint)
    if not path.exists():
        sys.exit(f"not found: {path}")
    try:
        bp = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        sys.exit(f"invalid YAML: {e}")
    if not isinstance(bp, dict):
        sys.exit("blueprint must be a mapping")

    run_checks(bp)

    for e in ERRORS:
        print(f"ERROR  {e}")
    if not args.quiet:
        for w in WARNINGS:
            print(f"warn   {w}")

    digest = compute_hash(bp)
    if args.hash and not ERRORS:
        if bp.get("blueprint_hash") != digest:
            text = path.read_text(encoding="utf-8")
            if "blueprint_hash:" in text:
                text = re.sub(r"^blueprint_hash:.*$", f"blueprint_hash: {digest}", text, flags=re.M)
            else:
                text = re.sub(
                    r"^(blueprint_version:.*)$", rf"\1\nblueprint_hash: {digest}", text, flags=re.M
                )
            path.write_text(text, encoding="utf-8")
            print(f"\nwrote blueprint_hash: {digest}")
    elif bp.get("blueprint_hash") and bp["blueprint_hash"] != digest:
        print(f"\nwarn   blueprint_hash is stale. Recompute with --hash.")

    print(f"\n{len(ERRORS)} error(s), {len(WARNINGS)} warning(s)")
    if ERRORS:
        sys.exit(1)
    if WARNINGS and args.strict:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
