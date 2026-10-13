---
name: blueprint
description: Design the knowledge-graph blueprint for a corpus before anything is ingested. Use this when someone wants to turn documents (laws, regulations, manuals, books, recipes, policies, standards, technical documentation) into a knowledge graph or GraphRAG system and there is no blueprint yet, or when an existing blueprint needs revising because extraction quality is poor, the graph came out as a hairball, entities are duplicated, or retrieval misses obvious answers. Covers corpus inspection, structure discovery, ontology design, embedding strategy, the query workload the graph must serve, and safety guardrails. Produces a spec-valid blueprint.yaml that `forge add` consumes unchanged, proves it chunks the real corpus, and stops for sign-off; it never runs extraction against a real model, writes to a graph database, or ingests a corpus.
---

# Blueprint

Design time. You decide what the graph should be, write it down in a form a
machine can execute, prove the machine accepts it, and stop.

Two other components consume what you produce. **Forge** builds the graph from
it, unattended. **Serve** answers questions from it. Neither makes ontology
decisions. If either would have to decide what something *means*, the blueprint
is incomplete and that is your bug, not theirs.

All commands below run from the repo root. Paths inside a blueprint resolve
against the working directory, so this is not optional.

## The one rule

**You never run extraction against a real model, write to a graph database, or
ingest a corpus.**

You read a sample, propose, prove it chunks, and stop for sign-off. Extraction is
where money is spent and where a wrong ontology stays hidden for weeks. The
sign-off gate is not politeness; it is the only cheap moment to be wrong.

The one Forge run you do make (step 8) uses the mock extractor and a throwaway
registry. It calls no model and costs nothing; it exists because only Forge's
own loader and chunker can show what Forge will produce.

## What you produce

| File | What it is |
|---|---|
| `blueprints/<name>.blueprint.yaml` | The contract. Validates against `spec/blueprint.schema.json` and the Forge contract checks |
| `vocab/<label>.csv` | Seed rows for each controlled vocabulary. Header `canonical,aliases`, aliases separated by `|` |
| `eval/<name>.jsonl` | The golden set, 30 to 50 labelled questions |

The *why* goes in the blueprint's `design_note` field, not a separate file, so it
is hashed and versioned with the types it explains. In six months someone will
ask why two types were not merged, and the answer will be gone. Write a short
paragraph per contested decision: what the type is, what question it answers,
and what you considered merging it with.

**Start from an example, never from a blank file.** Copy the reference blueprint
closest to the corpus and edit it. Writing YAML from memory is how fields end up
misspelled or invented, and the spec rejects unknown fields.

| Example | Corpus shape |
|---|---|
| `spec/examples/firecraft.blueprint.yaml` | PDF books, ALL-CAPS section headings, safety guardrails, multi-vector |
| `spec/examples/tenancy.blueprint.yaml` | Markdown statute, numbered Parts and Articles, vocabulary-anchored parties |

## Sequence

Work in this order. Each step feeds the next, and skipping ahead to the ontology
is the single most common way to produce a blueprint that looks reasonable and
does not survive contact with the corpus.

### 1. Look at the actual text

```bash
python blueprint/scripts/inspect_corpus.py --input <path> --show-sample
```

Never design from a filename, a title, or domain knowledge. Read real text. The
inspector reports heading families, proposes hierarchy regexes with matched
examples, flags front matter and indexes, strips page furniture, and lists
recurring noun phrases.

If it reports **NO TEXT LAYER**, stop. The document is scanned and needs OCR
first; everything downstream is worthless until that is fixed.

If it reports **NO HEADING STRUCTURE DETECTED**, read the sample yourself before
proposing anything. Continuous prose is a legitimate answer and changes the
chunking strategy entirely.

Treat the proposed regexes as candidates, not conclusions. The inspector's
generic patterns (`^#\s+(.+)$`) also match titles, contents pages and appendix
headings. Prefer patterns anchored on what makes a real heading distinctive (a
keyword and a number: `^##\s+Article\s+(\d+)\s*-\s*(.+)$`). Every `example` must
be a line copied from the corpus, never typed from memory.

If the source format is `docx` or `epub`, Forge cannot load it. Say so now and
ask for a markdown or PDF conversion before designing anything.

### 2. Establish the workload before the ontology

This is the step people skip, and skipping it is why so many knowledge graphs
are elegant and useless.

Ask the user: **what questions must this answer?** Get 5 to 10 real ones, in
their words. Then sort them into shapes: lookup, multi-hop, aggregation,
negative, safety, comparison, temporal.

Two checks worth applying immediately:

- If none of the real questions need more than one hop, say so plainly. A vector
  store is cheaper, simpler and probably better. Recommending against building a
  graph is a legitimate and useful outcome of this skill.
- If every question is a lookup, the same applies.

The workload is what makes the ontology falsifiable. Without it, "is this a good
schema?" has no answer and the design conversation drifts into boxes and arrows.
See `references/workload_and_eval.md`.

### 3. Propose the ontology

Now, and not before. Derive types from the questions, then confirm they actually
appear in the corpus sample. The reverse order (types from frequency, questions
later) produces a graph that describes the text instead of serving the user.

Working ranges: 6 to 12 node types, 8 to 15 relationship types, an exhaustive
pattern list. For each node type, name the question it answers in `answers`. A
type that cannot name one is a property.

The pattern list is enforced, not advisory. Forge drops every extracted edge
whose `[source, RELATION, target]` is not declared, so a missing pattern is an
edge the graph will never have. Walk each workload shape's `must_traverse` and
check every hop has a pattern in the direction the question needs.

Read `references/ontology_design.md` and `references/forge_contract.md` before
this step. The first covers granularity, domain/range discipline, provenance and
temporal validity. The second says what Forge actually does with each field,
including the parts of the spec it does not execute yet.

### 4. Vocabularies

For each type with many surface forms (parties, ingredients, drug names, part
numbers), write `vocab/<label>.csv` and point `ontology.vocabularies.<Label>.source`
at it. Forge resolves only on exact and fuzzy match today, so the aliases you
write are most of the resolution quality. Seed from an authoritative list where
one exists.

### 5. Embedding strategy

What gets vectorised is a design decision, not an implementation detail.

Decide which nodes get vectors, what text template builds each one, whether
facets get separate vectors, and which model. Templates are restricted to the
fields Forge supplies:

- Chunk targets: `{path}` and `{text}`. Always include `{path}`; `Article 12`
  without its breadcrumb is ambiguous.
- Entity targets: `{name}` and `{label}`.

Provider must be `local`, and `dimensions` must match the model. See
`references/embedding_strategy.md`.

### 6. Guardrails and the golden set

Anything that must never leak (allergens, dietary flags, jurisdiction, in-force
dates, tenant visibility, safety warnings) is declared in `guardrails` and
compiled into Cypher by Serve. A prompt-level constraint can be talked around; a
`WHERE` clause cannot.

Decide the conflict policy. Sources will disagree. `surface_both` with
attribution is the safe default; a safety corpus may want the stricter rule to
win, a statute corpus the newest. That is a domain decision and it belongs in
the blueprint. See `references/guardrails.md`.

Then draft the golden set: 30 to 50 questions with the chunk or node that should
be retrieved, covering every declared shape, including at least one negative
and, where relevant, one safety question. Generating candidates from the corpus
is fine and fast, but review them: generated questions are biased toward what is
easy to retrieve, which is exactly the bias you are trying to measure.

### 7. Validate

```bash
python blueprint/scripts/validate_blueprint.py blueprints/<name>.blueprint.yaml --hash
```

Fix every error. Errors from the forge contract section mean Forge would fail on
this blueprint, unattended, after spend; they are never acceptable. Read every
warning and either fix it or say in the hand-off why you accept it. Warnings that
start "Forge does not ..." name a declared feature that will silently do nothing;
the user needs to hear those, not discover them.

### 8. Prove it chunks

Run the blueprint through Forge's own loader and chunker on one or two real
source files, with the mock extractor and a scratch registry:

```bash
forge --store .blueprint-check/forge.db add <sample file> \
      --blueprint blueprints/<name>.blueprint.yaml --provider mock
forge --store .blueprint-check/forge.db list
forge --store .blueprint-check/forge.db chunks <source_id>
rm -rf .blueprint-check
```

The inspector uses its own regexes and cleanup, so a structure that looked right
there can still be wrong here. Check:

- the chunk count is in the range the corpus implies (one per unit at `chunk_at`)
- every path is a real breadcrumb, and no chunk path is empty
- no chunk comes from a contents page, index or front matter
- no chunk ends mid-sentence at exactly `max_chunk_chars` (truncation: add a
  `subsplit_pattern`)

If any check fails, fix `structure` and go back to step 7. Never point this at
the user's graph store or a real extraction provider.

### 9. Present, then stop

Present to the user:

- the structure, with real matched examples and the chunk count from step 8
- each node and relationship type, with the question it answers
- the vocabularies and how many rows each starts with
- the embedding plan
- the guardrails and conflict policy
- every validator warning you accepted, and why
- what you deliberately left out, and why
- an honest estimate of which workload shapes this will serve well and which it
  will not

**Then stop and ask for sign-off.** Do not proceed to extraction. Do not offer
to start ingestion in the same breath. Hand over the commands and let the user
decide when to spend:

```bash
forge add <source> --blueprint blueprints/<name>.blueprint.yaml --provider openai --dry-run   # cost estimate
forge add <source> --blueprint blueprints/<name>.blueprint.yaml --provider openai
forge cook <source_id> --blueprint blueprints/<name>.blueprint.yaml
```

## Judgment calls that come up every time

**"Should X be a node or a property?"** A node if it could be an answer unit and
it recurs across documents. Otherwise a property or an edge attribute. Quantity
is not a node. A date is not a node unless users ask what changed in 2019.

**"The corpus mentions Y a lot, shouldn't it be a type?"** Frequency shows what
the corpus talks about, not what users ask for. Check Y against the workload. If
no question needs it, it is background.

**"Can we add more types to be safe?"** No. Past roughly 15 node types the
extractor is choosing between near-synonymous labels and the choice becomes
arbitrary. More types is worse extraction, not richer data.

**"Two sources disagree."** That is data, not a problem to resolve at design
time. Set the conflict policy and let Forge record both with attribution.

**"The spec allows it, so Forge must do it."** Not yet, in several places:
cross-reference parsing, the embedding and LLM resolution tiers, rich entity
cards, docx and epub. `references/forge_contract.md` is the list. Design around
the gap and name it in the hand-off; never patch around it in Forge from here.

**"The user wants to start now."** The inspector run and the step 8 check cost
nothing and take a minute. Do them. Designing from a description of the corpus
rather than the corpus is the most expensive shortcut available here.

## What you do not do

- Extract with a real model, ingest, or write to any graph store. That is Forge.
- Write retrieval queries or tune traversal. That is Serve.
- Change Forge to accept a blueprint. If Forge cannot execute something, the
  blueprint changes, or the gap is reported. That is the invariant.
- Decide what a cheaper model would do to quality. That is a Forge measurement,
  made against the golden set you produced.
- Accept an open ontology. `closed: true` is not negotiable; an open schema is
  the single most common cause of a hairball graph.
