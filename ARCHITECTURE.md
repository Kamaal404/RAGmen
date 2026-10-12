# Architecture: splitting GraphRAG into three components

Status: proposal. Supersedes the single-repo design in `graphrag-builder`.

## 1. Why split, and where the line actually falls

The instinct to split is right, but "read data" vs "build graph" vs "answer" is
not the deepest reason. The real line is **cadence**, and cadence determines
everything else about how a component should be built.

| | Design time | Build time | Query time |
|---|---|---|---|
| Runs | Once per corpus type | Once per source, repeatedly | Per user question |
| Driven by | Human judgment | A machine, unattended | A user, waiting |
| Correct behaviour | Ask, propose, stop for sign-off | Never ask, never improvise | Answer or say it cannot |
| Failure mode | A bad ontology, discovered weeks later | Partial or duplicated data | A confident wrong answer |
| Determinism | Deliberately non-deterministic | Must be fully deterministic and idempotent | Deterministic given the graph |
| Cost shape | Cheap, human time dominates | Expensive, token-dominated | Cheap per call, latency-dominated |

These three want opposite things. A component that pauses to ask a clarifying
question is correct at design time and broken at build time. A component that
retries silently and moves on is correct at build time and dangerous at design
time. Merging them means one of the two behaviours is always wrong.

That is also why the current monolith resists being a clean skill: a skill is a
body of judgment, and roughly half of what is in it now is deterministic
plumbing that should never involve judgment at all.

**The split, stated once:**

- **Blueprint** decides *what the graph should be*. Judgment. Human-gated.
- **Forge** makes it so, repeatedly and identically. Machinery. Unattended.
- **Serve** answers from it. Read-only. Latency-bound.

## 2. The contract between them

The whole architecture stands on one artifact. If the contract is good, the
three parts can be built by different people, replaced independently, and tested
in isolation. If it leaks, you have a distributed monolith with extra steps.

```
                    ┌──────────────────────┐
   raw sample  ───► │  1. BLUEPRINT        │  skill, human-in-loop
                    │     (design time)    │
                    └──────────┬───────────┘
                               │ blueprint.yaml  ◄── the only contract
                               │ (versioned, hashed, schema-validated)
                               ▼
   sources     ───► ┌──────────────────────┐
   (add/update/     │  2. FORGE            │  CLI + service + job ledger
    retract)        │     (build time)     │
                    └──────────┬───────────┘
                               │ graph + vectors + source registry
                               ▼
   questions   ───► ┌──────────────────────┐
                    │  3. SERVE            │  MCP server, read-only
                    │     (query time)     │
                    └──────────┬───────────┘
                               │ cited answers
                               ▼
                            the agent

            drift report ──────────────────┐
            (Forge → Blueprint)            │  the feedback loop, section 8
            ◄──────────────────────────────┘
```

**Rule: Forge and Serve never make ontology decisions.** If either needs to
decide what something *means*, the blueprint is incomplete. That is the test for
whether the boundary is holding.

## 3. The Blueprint

This is the deliverable of component 1 and the input to everything else. It is
not a config file; it is a specification, and it should be validated against a
JSON Schema and refuse to load if invalid.

Your original framing called it "the blueprint of the embedding". That is right
and it is bigger than the ontology. A blueprint has five sections:

```yaml
blueprint_version: 1.2.0          # semver; bump on any change
blueprint_hash: sha256:a3f2...    # content hash, stamped on every node written
name: firecraft
description: Practical fire-building and bushcraft manuals

# --- 1. STRUCTURE: how raw sources become chunks -------------------------
structure:
  source_types: [pdf, markdown, html]
  hierarchy: [...]                # regex patterns per level
  chunk_at: section
  max_chunk_chars: 5000
  skip_regions:                   # front matter, TOC, index: pay for nothing
    - {match: '^(CONTENTS|INDEX|ACKNOWLEDG)', span: to_next_heading}
  xref_patterns: [...]

# --- 2. ONTOLOGY: what the graph contains --------------------------------
ontology:
  node_types: [...]
  relationship_types: [...]
  patterns: [...]                 # domain/range signatures, exhaustive
  closed: true                    # all three additional_* flags false
  vocabularies:                   # controlled lists, resolved against not inferred
    Material: {source: vocab/materials.csv, tiers: [exact, fuzzy, embedding, llm_closed]}

# --- 3. EMBEDDING: what gets vectorised and how --------------------------
embedding:
  model: all-MiniLM-L6-v2
  dimensions: 384
  targets:
    - name: chunk_verbatim
      of: Chunk
      text: "{path}\n\n{text}"    # hierarchy path included: 'Article 12' alone is ambiguous
    - name: entity_card
      of: [FireLay, Technique]
      text: "{name}. {description}. Used for: {purposes}. Avoid when: {avoid_when}"
  multi_vector: true              # separate facet vectors beat one blended vector
  cache: content_hash             # never re-embed identical text

# --- 4. WORKLOAD: what questions this graph must answer ------------------
#     This is what makes the ontology falsifiable. Without it, "is this a good
#     schema?" has no answer.
workload:
  shapes:
    - id: lookup
      example: "what is a Dakota fire hole"
      expect: {retriever: hybrid, hops: 0}
    - id: multi_hop
      example: "what do I burn for a cooking fire in wet conditions"
      expect: {retriever: hybrid_cypher, hops: 2}
    - id: negative
      example: "which fire lays should I avoid in high wind"
      expect: {must_traverse: [UNSUITED_FOR]}
    - id: safety
      example: "is it safe to build this in a snow shelter"
      expect: {must_return: [Hazard, SafetyRule]}
  golden_set: eval/firecraft.jsonl   # 30-50 labelled Q/A, built at design time

# --- 5. GUARDRAILS: what must never leak ---------------------------------
guardrails:
  hard_filters:                   # enforced in Cypher, never in a prompt
    - {name: visibility, cypher: "node.visibility = 'public' OR node.owner_id = $user_id"}
    - {name: hazards_always, mode: force_include, labels: [Hazard, SafetyRule]}
  release_blockers: [safety_leak_suite]
```

Sections 4 and 5 are the ones normally missing, and they are the ones that make
the difference between a demo and something another person can trust.

**Section 4 turns ontology design from taste into engineering.** Right now, the
only way to know a schema is wrong is to build the whole graph and be
disappointed. Declaring the query shapes up front means every later change is
measurable, and it forces the design conversation to be about questions rather
than about boxes and arrows.

**Section 5 carries safety across the boundary.** A hard filter decided at design
time must be mechanically enforced at query time. Writing it into the blueprint
means Serve cannot forget it and a prompt cannot talk around it.

## 4. Component 1: Blueprint (the skill)

A Claude skill. Judgment, conversation, and a hard stop.

**Does:** read a real sample of the corpus, find the structure, propose an
ontology with justification per type, propose the embedding strategy, draft the
golden set, and stop for sign-off.

**Never:** ingest a full corpus, write to a database, or run extraction at scale.

**Why a skill and not code:** every step is a judgment call that depends on
reading the material. What counts as an answer unit, whether two concepts are one
node, whether a distinction is worth a type. That is not automatable, and
pretending it is produces the hairball.

Outputs: `blueprint.yaml`, `vocab/*.csv` seeds, `eval/<name>.jsonl`, and a short
design note recording *why* each type exists. The design note matters more than
it looks: in six months the question will be "why is Condition separate from
Purpose" and nobody will remember.

The sign-off gate is the point of the whole component. Extraction is where money
is spent and a wrong ontology is discovered three weeks and several hundred
dollars later.

## 5. Component 2: Forge (the builder)

A Python package with a CLI and an optional HTTP service. **Not an MCP server**,
for a reason worth stating: MCP is a request/response tool protocol for agents,
and ingestion is a long-running, resumable, expensive batch job. Modelling a
two-hour ingest as a tool call is the wrong shape. Expose a *thin* MCP wrapper
with `add_source`, `job_status` and `retract_source` so an agent can trigger and
monitor work, but the work itself lives behind a job queue.

**Does:** everything deterministic. Load, clean, chunk, extract against the
blueprint, resolve entities, embed, write, validate, report.

**Never:** decide what a type means, invent a label, or ask a question.

### 5.1 What "prepared to add sources from the user" actually requires

This is the hardest part of the whole system and the part most GraphRAG projects
get wrong, because the first version only ever ingests one fixed corpus. Five
things are needed and none of them are optional once a second source exists:

**A source registry.** Every source is a first-class row, not just a filename:

```
source_id, uri, content_hash, mime, added_by, added_at,
visibility, license, blueprint_version, status, chunk_count, cost_usd
```

**Content addressing.** Chunk IDs derive from `hash(source_id + path + text)`.
Re-uploading an unchanged source is a no-op that costs nothing. Re-uploading a
changed source re-extracts only the chunks whose hashes changed. For a book with
a corrected edition, that is the difference between minutes and hours.

**Retraction with refcounting.** When a user removes a source, its chunks must
go, but `Ingredient: birch bark` is shared with four other sources and must
survive. Every entity carries an incoming `ASSERTED_BY` edge per source; delete
the source, decrement, remove the entity only at zero. Without this, deletion
either leaves orphans or silently destroys other users' data.

**Scoped entity resolution.** Resolution on incremental add must compare new
entities against the canonical set, never re-resolve the whole graph. Unscoped
resolution on a growing store is slow and, worse, can re-merge things someone
deliberately separated. Anchor to the vocabulary; do not drift pairwise.

**Conflict representation.** Two sources will disagree: different substitution
ratios, contradictory safety advice, superseded regulations. The graph must
*represent* the disagreement rather than let last-write-wins pick a winner.
Edges carry `asserted_by: [source_id]`, and Serve surfaces both with
attribution. This is the single biggest credibility difference between a toy and
something people rely on, and it is nearly impossible to retrofit.

### 5.2 Resumability

A 2000-chunk ingest that dies at chunk 1800 must resume at 1800. A job ledger
with per-chunk status turns a catastrophe into a retry. Same ledger gives you
per-source cost accounting for free, which you will want the first time someone
asks what a corpus cost to build.

### 5.3 Stage boundaries stay

The current dry-run-then-write split survives the refactor and gets stronger:
extraction output is an inspectable artifact before it touches the store, and it
reloads without any LLM spend. Keep it.

## 6. Component 3: Serve (retrieval) and the agent

**Serve is an MCP server.** This is exactly what MCP is for: a small set of
typed tools an agent calls, read-only, low latency.

Tools:
- `search(query, filters, top_k)` — hybrid retrieval plus blueprint traversal
- `expand(node_id, hops)` — neighbourhood on demand
- `cite(chunk_id)` — verbatim span plus source and page
- `explain_path(a, b)` — how two things connect, for "why did you say that"
- `schema()` — the ontology, so the agent can form structured queries

**Split Serve from the agent.** Serve is deterministic retrieval with no model in
it. The agent is prompt, persona and answer policy. Keeping them separate means
retrieval is measurable on its own (recall@k against the golden set) and the
agent can be swapped without touching the graph. Conflating them is why so many
RAG systems cannot tell you whether a bad answer was a retrieval miss or a
generation failure.

**The agent's answer policy**, declared not improvised:
- answer only from retrieved context, cite every claim
- surface conflicts rather than silently choosing
- say "not in the corpus" instead of falling back to general knowledge, which for
  a corpus someone deliberately built a graph from is the whole point
- never bypass a hard filter, because it was never the agent's to bypass

## 7. Cross-cutting concerns

**Blueprint versioning.** Every node and edge is stamped with the
`blueprint_hash` that produced it. When the blueprint changes, you can compute
exactly which types are affected and re-extract only those, rather than
rebuilding everything or, worse, silently mixing two ontologies in one graph.
This one line of metadata saves the project later.

**Provenance everywhere.** `source_id`, `chunk_id`, `char_start`, `char_end`,
verbatim `quote`. Carried from Forge through Serve to the answer. For any corpus
where "where does it say that" is legitimate, an uncited answer is not an answer.

**Multi-tenancy from day one.** `visibility` and `owner_id` on every node.
Retrofitting tenant isolation into a graph is a rewrite, and user-uploaded
sources make it a licensing question too, not just a privacy one.

**Evaluation as infrastructure.** The golden set from the blueprint runs in CI.
Measure retrieval (recall@k on the gold chunk) separately from generation.
Always baseline against plain vector RAG: GraphRAG costs more to build and run,
and if it does not beat the baseline on the declared workload, the schema is
wrong and more graph will not fix it.

## 8. The feedback loop, which closes the system

Forge sees things Blueprint never will: the long tail of real sources. Make that
observable and route it back.

Forge emits a **drift report** per ingest:
- *unmapped mention rate* — spans the extractor flagged as meaningful but that fit
  no declared type. Rising rate means the ontology is going stale.
- *pattern violation rate* by type — which relationships the model keeps trying to
  make that the blueprint forbids. Sometimes the model is right.
- *vocabulary miss rate* — surface forms that hit the LLM fallback tier. These are
  the next vocabulary entries.
- *hub formation* — resolution failures, caught early rather than at query time.

When any crosses a threshold, that is a signal to re-open the blueprint with
evidence attached. This turns ontology maintenance from a vague worry into a
triggered, evidence-backed task, and it is the thing that keeps the system alive
past month three.

## 9. Cost optimisations worth building in

Ordered by impact, measured against the current pipeline where the schema is
**40 to 97% of input tokens** because it is re-sent on every single chunk.

1. **Prompt caching on the schema block.** The blueprint's ontology section is
   byte-identical across every chunk in a run. Cached, it is charged once instead
   of N times. Given the overhead measured above, this is the single largest
   available saving, and it needs the prompt laid out cache-prefix-first. Design
   for it now; retrofitting means rewriting the prompt assembly.
2. **Batch API for extraction.** Ingestion is not latency sensitive. Roughly half
   price for accepting asynchrony you do not care about.
3. **Skip non-content regions.** TOC, index and front matter match heading
   patterns and produce chunks you pay to extract nothing from. Declared in
   `structure.skip_regions`, costing zero tokens.
4. **Chunk dedup by content hash.** Boilerplate repeats heavily in legal and
   technical corpora. Extract once, reference many.
5. **Tiered extraction.** Cheap model over everything, then route only
   low-confidence chunks (schema violations, missing quotes, zero extractions) to
   a stronger model. Most chunks are easy; paying premium rates for all of them
   to handle the hard 10% is the default mistake.
6. **Embedding cache keyed by content hash**, and local embeddings where quality
   allows. Re-embedding unchanged text on every re-ingest is pure waste.
7. **Verifier pass only on cross-chunk edges.** The expensive second pass is only
   needed for the tier that actually needs it, not for everything.
8. **Deterministic-first, always.** The cheapest token is the one not spent:
   hierarchy, numbering and cross-references are parseable at near-100% precision.

Items 1, 3 and 4 together should cut a typical book ingest by well over half
before any model change. Say that to anyone who opens with "which cheaper model
should we use".

## 10. Repository layout

One repo, three packages. A monorepo here because the blueprint schema is shared
and versioning three repos in lockstep early on is friction with no payoff.
Split later if a component genuinely gets its own release cadence.

```
<project>/
├── spec/
│   ├── blueprint.schema.json      # THE contract; both sides validate against it
│   ├── CHANGELOG.md               # contract changes are breaking changes
│   └── examples/
├── blueprint/                     # component 1, the skill
│   ├── SKILL.md
│   ├── references/                # ontology design, embedding strategy, workload design
│   └── commands/
├── forge/                         # component 2, the builder
│   ├── pyproject.toml
│   ├── src/forge/
│   │   ├── loaders/               # pdf, html, markdown, docx
│   │   ├── chunking/
│   │   ├── extract/               # blueprint-driven, prompt-cache aware
│   │   ├── resolve/
│   │   ├── embed/
│   │   ├── store/                 # neo4j adapter behind an interface
│   │   ├── registry/              # sources, jobs, refcounts
│   │   └── report/                # validation + drift
│   ├── cli.py
│   └── mcp_thin.py                # add_source / job_status / retract_source
├── serve/                         # component 3, retrieval MCP
│   ├── src/serve/
│   │   ├── retrievers/
│   │   ├── filters/               # guardrails compiled from the blueprint
│   │   └── mcp_server.py
├── agent/                         # answer policy, thin on purpose
├── blueprints/                    # per-corpus: firecraft, legal, recipes
├── eval/                          # golden sets + harness, runs in CI
└── deploy/                        # compose, migrations
```

Keep the store behind an interface in `forge/store/`. Not because you will
switch off Neo4j, but because it is what makes Forge testable without a database
and keeps Cypher from leaking into the extraction code.

## 11. Build order

Each phase ends with something demonstrable. Do not build ahead.

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | `spec/blueprint.schema.json` + migrate `firecraft.yaml` to it | Both sides validate the same file |
| 1 | Forge: load → chunk → registry, no LLM | Re-adding a source is a no-op; retraction refcounts correctly |
| 2 | Forge: extraction with prompt caching + dry-run artifact | Cost report shows the cache saving |
| 3 | Forge: resolve, embed, write, validate | Drift report emitted |
| 4 | Serve: MCP with hybrid + traversal + guardrails | Golden set recall@k measured |
| 5 | Agent: answer policy, citations, conflict surfacing | End to end on the fire book |
| 6 | Blueprint skill rebuilt to emit spec-valid blueprints | A new corpus onboards without touching Forge |
| 7 | Incremental: update, retract, conflict, drift thresholds | Second source added without a rebuild |

Phase 1 before phase 2 is deliberate. Source identity and retraction are
load-bearing and nearly impossible to retrofit, and they need no LLM to build or
test. Phase 6 last is also deliberate: the skill should be written against a
Forge that actually exists, or it will specify things Forge cannot consume.

## 12. Naming

The components need names that say what they do. A suggestion, take or leave:

- Project: **Lattice**
- `lattice-blueprint` — the skill
- `lattice-forge` — the builder
- `lattice-serve` — the retrieval MCP
- `lattice-agent` — the answer layer

Whatever you pick, name them after their *cadence role*, not their technology.
"neo4j-loader" ages badly the moment the store changes; "forge" does not.

## 13. Decisions still open

1. **Store.** Neo4j is the assumption. Worth a deliberate look at whether the
   vector half lives in Neo4j or a dedicated store, since that choice is much
   cheaper to make now than after phase 4.
2. **Forge service or CLI only.** CLI is enough until multiple users add sources
   concurrently. The job ledger makes the upgrade cheap either way.
3. **Where the golden set comes from.** Hand-written is higher quality;
   LLM-generated from chunks is faster and biased toward what is easy to
   retrieve. A hybrid, with human review of generated items, is probably right.
4. **Global search.** Community detection and summarisation is a large build with
   super-linear index cost. Only worth it if genuinely global questions are a
   requirement. Most systems need only local traversal. Defer, but do not design
   it out.
5. **Conflict policy.** Surfacing both sides is the safe default, but for a safety
   corpus you may want the stricter rule to win. That is a domain decision and it
   belongs in the blueprint's guardrails section, not in Serve.
