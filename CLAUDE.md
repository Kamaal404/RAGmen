# AGENTS.md

Context for any agent working in this repo. Read before writing code.

## What this is

RAGmen turns a document corpus into a knowledge graph and a retrieval layer.
It is split into three components **by cadence**, not by function:

| Component | Runs | Nature | Status |
|---|---|---|---|
| **Blueprint** | Once per corpus type | Judgment, human-gated | done |
| **Forge** | Once per source, repeatedly | Machinery, unattended | phases 1-5 done |
| **Serve** | Per question | Read-only retrieval, MCP | done |
| **Agent** | Per question | Answer policy layer | done |

Cadence determines correct behaviour. A component that pauses to ask a
clarifying question is right at design time and broken at build time. One that
retries silently and moves on is right at build time and dangerous at design
time. Do not blur these.

`ARCHITECTURE.md` has the full design and the phase plan.

## The one invariant

**Forge and Serve make no ontology decisions.** They read a validated blueprint
and execute it. If either would need to decide what something *means*, the
blueprint is incomplete; fix the blueprint or the spec, never patch around it in
Forge.

Corollary: no domain-specific branching in Forge or Serve code. If you are about
to write `if corpus == "legal"`, the spec needs a new field instead.

## Layout

```
spec/blueprint.schema.json               THE contract. Both sides validate against it.
spec/examples/firecraft.blueprint.yaml   Reference blueprint, validates clean.
blueprint/SKILL.md                       Design-time skill (Claude Code / opencode)
blueprint/references/                    ontology, workload+eval, embedding, guardrails
blueprint/scripts/inspect_corpus.py      Structure discovery. No LLM, no network.
blueprint/scripts/validate_blueprint.py  Spec + semantic validation, content hashing
blueprint/commands/                      /bp-new, /bp-revise
eval/firecraft.jsonl                     Golden set example
forge/                                   Build-time package (phases 1-5)
serve/                                   MCP retrieval server
agent/                                   Answer policy layer
```

## Running things

```bash
# Install all components
make setup

# Blueprint validation (no LLM, no DB)
python blueprint/scripts/validate_blueprint.py spec/examples/firecraft.blueprint.yaml

# Inspect a corpus before designing anything
python blueprint/scripts/inspect_corpus.py --input <corpus> --show-sample

# Run all tests (offline, no API key, no DB)
make test

# Forge CLI
forge add <file> --blueprint spec/examples/firecraft.blueprint.yaml
forge list
forge cook <source_id>
forge retract <source_id>
forge conflicts

# Serve
lattice-serve --store memory --blueprint spec/examples/firecraft.blueprint.yaml

# Agent
agent ask "your question here"
```

Tests must pass before any commit. They are fast and need no credentials, so
there is no excuse for skipping them.

## Conventions

- Python 3.11+, stdlib first. Add a dependency only when it earns its place.
- Scripts are single-purpose and runnable standalone with `--help`.
- Every script works offline unless it is explicitly a network stage.
- Comments explain *why*, not *what*. Especially for non-obvious guards: most of
  them are there because something failed in testing.
- No em dashes in prose or docs.
- Shell scripts and Makefiles stay LF. `.gitattributes` enforces this; Windows
  CRLF has already broken this project once.
- New validation rule means a new mutation case in `tests/test_blueprint.sh`. The
  suite tests that the validator **rejects** bad input; a validator only ever
  tested on good input has not been tested.

## Bugs already found and fixed, do not reintroduce

These came out of testing and are easy to write again:

1. **`re.split` with capture groups** returns the captured groups as list
   elements. Measuring chunk sizes that way reported a median of 11 characters.
   Measure from `finditer` positions instead.
2. **ALL-CAPS heading regex `[A-Z][A-Z0-9'\-]{3,}`** needs a 4-character first
   word, so `THE DAKOTA FIRE HOLE` never matched. Use `{2,}`.
3. **Heading patterns starting with `\s*` under `re.MULTILINE`** match on the
   newline *before* the heading. Chunk offsets land one character early, which
   pushes page-boundary chunks onto the previous page. Advance past leading
   whitespace.
4. **Page offsets must be recorded after cleanup**, not before. Dehyphenation and
   reflow shorten text and shift every boundary.
5. **Reflow by line width, not punctuation.** Measure the width statistic before
   any joining, or already-joined lines inflate it and block the rest.
6. **Low-confidence hierarchy levels must be re-sorted by rank** after merging.
   Hierarchy is outermost-first; appending at the end produces an invalid order.

## Current state

Phases 1-5 are complete and tested:

- **Phase 1**: source registry, loaders (PDF, text, markdown), chunking, CLI (`forge add/list/status/retract/chunks`)
- **Phase 2**: extraction with prompt caching, dry-run, cost reporting (`forge extract`)
- **Phase 3**: entity resolution (tiered: exact → fuzzy → embedding → LLM), embedding with hash cache, graph storage (MemoryStore + Neo4j), pipeline orchestration, drift reporting
- **Phase 4**: Serve MCP server with hybrid retrieval (vector + keyword + graph, RRF fusion), guardrail compiler
- **Phase 5**: Agent answer policy with enforced citations, conflict surfacing, corpus-only answers

## Next task: Phase 6 — Blueprint skill rebuild

Rebuild the design-time blueprint skill now that Forge exists to consume its output.
The skill must emit spec-valid blueprints that `forge add` can use directly.
See `blueprint/SKILL.md` for the current design.

## Deliberately not done yet

- Phase 6: Blueprint skill rebuild (next)
- Phase 7: Evaluation harness in CI (golden set + recall@k)
- Global search via community detection (only worth it for genuinely global queries)
- Deployment manifests (Docker Compose for forge + serve stack)

## Cost facts worth remembering

The ontology block is re-sent on every chunk and measured at 40 to 97% of input
tokens on real corpora. Therefore, in order of impact:

1. Prompt caching on the schema prefix. Already implemented in phase 2.
2. Batch API. Ingestion is not latency sensitive.
3. Skip regions (already in phase 1).
4. Chunk dedup by content hash (already in phase 1).
5. Tiered extraction: cheap model everywhere, strong model only on low-confidence
   chunks.

Switching to a cheaper model is far down this list. Say so when someone opens
with that question.
