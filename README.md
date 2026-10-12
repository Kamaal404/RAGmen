<div align="center">

<img src="assets/originals/ragmen-logo.png" alt="RAGmen" width="220"/>

# RAGmen

**Noodles are nodes. Instant GraphRAG without the long cooking.**

Turn a document corpus into a schema-grounded knowledge graph and a read-only retrieval layer. Three components split by cadence, not function: design it once, build it once per source, serve it per question.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![Neo4j](https://img.shields.io/badge/neo4j-2026.07-green)](https://neo4j.com)
[![MCP](https://img.shields.io/badge/MCP-read--only-orange)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

</div>

## The menu

| Command | What it does | Status |
|---|---|---|
| `forge add` | Register and chunk a source against a blueprint | working |
| `forge cook` | Full pipeline: resolve → embed → write to graph | working |
| `forge retract` | Remove a source with entity refcounting | working |
| `forge update` | Incremental re-extraction on changed sources | working |
| `forge conflicts` | List multi-source disagreements | working |
| `lattice-serve` | Read-only MCP: search, expand, cite, explain_path, schema | working |
| `agent ask` | Cited answer with conflict surfacing, corpus-only policy | working |
| *(blueprint)* | Design-time skill — see `blueprint/SKILL.md` | working |

## Quickstart

```bash
# 1. Install components
cd forge && pip install -e ".[dev]" && cd ..
cd serve && pip install -e ".[dev]" && cd ..
cd agent && pip install -e ".[dev]" && cd ..

# Or all at once:
make setup

# 2. Validate a blueprint
python blueprint/scripts/validate_blueprint.py spec/examples/firecraft.blueprint.yaml

# 3. Register and chunk a source
forge add corpus.pdf --blueprint spec/examples/firecraft.blueprint.yaml

# 4. Dry-run extraction (no LLM spend, writes prompts to disk)
forge add corpus.pdf --blueprint spec/examples/firecraft.blueprint.yaml --dry-run

# 5. Full pipeline (resolve → embed → write to in-memory store)
forge cook <source_id>

# 6. Start the MCP retrieval server
lattice-serve --store memory --blueprint spec/examples/firecraft.blueprint.yaml

# 7. Ask a question
agent ask "how do I build a dakota fire hole?"

# Run all tests (offline, no API key, no DB)
make test
```

## How it fits together

```
corpus sample → blueprint/ (design, human-gated) → blueprint.yaml
                                                         |
sources ──────────────────→ forge/ (build, unattended) ──┤
                                  register, chunk,       |
                                  extract, resolve,      v
                                  embed, write ──→ Neo4j / MemoryStore
                                                         |
questions ────────────────────────────────→ serve/ (query, read-only)
                                                  hybrid search (vector
                                                  + keyword + graph)
                                                         |
                                            agent/ (answer policy)
                                                  citations, conflicts,
                                                  corpus-only answers
```

**The one invariant:** Forge and Serve make no ontology decisions. They read a validated blueprint and execute it. If either needs to decide what something *means*, the blueprint is incomplete.

## Repo layout

```
ragmen/
  spec/                     THE contract: blueprint.schema.json + examples
  blueprint/                Design-time scripts and skill (human-gated)
  forge/                    Build-time package: load → chunk → extract → resolve → embed → write
  serve/                    Query-time MCP package: hybrid retrieval, guardrails
  agent/                    Answer policy: citations, conflict surfacing
  eval/                     Golden Q&A sets for retrieval regression
  assets/                   Logos and diagrams
  docker-compose.yml        Neo4j container (development + production)
  Makefile                  Common workflows
  ARCHITECTURE.md           Full design, phase plan, cost analysis
```

Each of `forge/`, `serve/`, and `agent/` has its own `pyproject.toml` and can be installed independently. In production you deploy `serve` and `agent` only — `forge` runs as a batch job or CI step.

## Neo4j

```bash
make up      # start Neo4j container
make shell   # cypher-shell into it
make down    # stop
make reset   # wipe and restart
```

Requires `.env` with `NEO4J_USER` and `NEO4J_PASSWORD`. See `docker-compose.yml` for version and APOC compatibility.

## Phases completed

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Blueprint schema + firecraft reference | done |
| 1 | Source registry, chunking, CLI (no LLM) | done |
| 2 | Extraction with prompt caching + dry-run | done |
| 3 | Resolve → embed → write → drift report | done |
| 4 | Serve: MCP + hybrid retrieval + guardrails | done |
| 5 | Agent: citations + conflict surfacing | done |
| 6 | Blueprint skill rebuild | next |
| 7 | Incremental update + conflict + drift CI | tests written |
