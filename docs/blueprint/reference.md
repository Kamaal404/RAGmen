# Blueprint Reference Example

The `firecraft` blueprint is the reference implementation that ships with RAGmen. Every feature of the spec is exercised here. Read it alongside this walkthrough.

File: `spec/examples/firecraft.blueprint.yaml`

---

## Header

```yaml
blueprint_version: 1.0.0
blueprint_hash: sha256:b0654b60ba440c9440a286e0df08416a2dec2194db4705e6737f36da45568187
name: firecraft
description: Practical instructional books on fire building, bushcraft and survival.
```

`blueprint_hash` is computed automatically by the validator and stamped on every extracted chunk. If you change the blueprint, chunks extracted under the old hash are targeted for re-extraction.

---

## Design note

```yaml
design_note: |
  Procedure is the central node because every declared question is ultimately
  "what do I build / do". Technique is separate from Procedure because ignition
  methods recombine freely across structures: a bow drill lights a teepee or a
  log cabin, and merging them would force a combinatorial explosion of types.
  ...
```

The `design_note` is not processed by the system — it is for humans. Write down *why* you made each design decision. You will thank yourself when revisiting the schema months later.

---

## Structure

```yaml
structure:
  hierarchy:
    - level: chapter
      pattern: '^\s*(?:CHAPTER|Chapter)\s+([IVXLCDM]+|\d+)\s*[-–—:.]?\s*(.*)$'
      example: "CHAPTER 4 - Tinder and Kindling"
    - level: section
      pattern: '^([A-Z][A-Z0-9''\-]{2,}(?:\s+[A-Z0-9''\-&]+){0,6})\s*$'
      example: "THE DAKOTA FIRE HOLE"
  chunk_at: section
  max_chunk_chars: 5000
  skip_regions:
    - match: '^\s*(CONTENTS|INDEX|ACKNOWLEDGMENTS|ABOUT THE AUTHOR|BIBLIOGRAPHY)'
      span: to_next_heading
      reason: Matches heading patterns and costs tokens to extract nothing.
```

**`hierarchy`** — the two-level structure of this book: chapters contain sections. Each level has:
- `pattern` — a regex that matches headings at that level (Python flavor)
- `example` — a sample heading from the document, for documentation

**`chunk_at: section`** — chunks are created at section boundaries. A section is one chunk. This preserves relationships that span multiple paragraphs within a section.

**`max_chunk_chars: 5000`** — sections longer than this are split at `subsplit_pattern` (step boundaries in this book).

**`skip_regions`** — patterns that identify sections to skip entirely before chunking. These would match heading patterns and cost real money to extract nothing useful.

---

## Ontology

### Node types

The firecraft blueprint has 8 node types:

| Label | What it represents | Central question |
|---|---|---|
| `Procedure` | Fire structures (teepee, log cabin, dakota hole) | What should I build? |
| `Technique` | Ignition methods (bow drill, ferro rod) | How do I light it? |
| `Material` | Things burned (birch bark, dry grass, hardwood) | What do I burn? |
| `Tool` | Equipment not consumed (knife, axe, lens) | What kit do I need? |
| `Purpose` | Goals (warmth, cooking, signaling) | Which fire suits my goal? |
| `Condition` | Environmental circumstances (rain, wind, wet wood) | What works in my situation? |
| `Hazard` | Specific dangers (carbon monoxide, ember scatter) | What could go wrong? |
| `SafetyRule` | Explicit safety instructions | How do I avoid that? |

!!! tip "Why Technique is separate from Procedure"

    A bow drill can light a teepee *or* a log cabin. Merging them would create a combinatorial explosion: "bow drill teepee", "bow drill log cabin", etc. Keeping them separate lets one technique connect to many procedures.

!!! tip "Why Material has a `role` property instead of three types"

    Birch bark can be tinder when shaved thin, or fuel when used in a log. The same material changes role by context. A `role` property captures this without creating three separate types.

### Relationships

```yaml
relationship_types:
  - label: REQUIRES_MATERIAL
    description: This procedure consumes this material at the given stage.
    properties:
      - {name: stage, type: STRING, enum: [tinder, kindling, fuel]}

  - label: SUITED_FOR
    description: Explicitly recommended by the text under this condition.

  - label: UNSUITED_FOR
    description: Explicitly warned against by the text under this condition.
```

!!! tip "SUITED_FOR and UNSUITED_FOR are separate, not one edge with a flag"

    The negative case is what a survival reader most needs. A boolean property (`suited: false`) is exactly what a graph traversal forgets to check. Two separate relationship types make the negative case a first-class citizen.

### Patterns

```yaml
patterns:
  - [Procedure, SERVES_PURPOSE, Purpose]
  - [Procedure, REQUIRES_MATERIAL, Material]
  - [Procedure, HAS_HAZARD, Hazard]
  - [Technique, IGNITES, Material]
  - [Technique, REQUIRES_TOOL, Tool]
  - [Hazard, MITIGATED_BY, SafetyRule]
  # ... 18 total
```

Every allowed connection is declared explicitly. The AI cannot create an edge like `[Material, HAS_HAZARD, Procedure]` because it is not in the pattern list. This is what prevents hallucinated connections.

---

## Guardrails

```yaml
guardrails:
  conflict_policy: prefer_strictest
  hard_filters:
    - name: hazards_always
      mode: force_include
      labels: [Hazard, SafetyRule]
      rationale: A method returned without its warning is worse than no answer.
    - name: source_visibility
      mode: restrict
      params: [user_id]
      cypher: "node.visibility = 'public' OR node.owner_id = $user_id"
      rationale: User-uploaded books are licensed to that user only.
  answer_policy:
    require_citation: true
    refuse_outside_corpus: true
```

**`prefer_strictest`** — when sources conflict on a safety question, the more cautious answer wins.

**`hazards_always`** — Hazard and SafetyRule nodes are always included in answers, regardless of the query. This is enforced in Cypher, not the system prompt — it cannot be overridden.

**`source_visibility`** — a row-level filter that prevents users from accessing sources they do not own. Enforced as a Cypher `WHERE` clause.

---

## Drift thresholds

```yaml
drift:
  max_unmapped_mention_rate: 0.15
  max_vocabulary_miss_rate: 0.10
  max_degree: 200
  min_provenance_coverage: 0.85
```

After each extraction run, `forge cook` reports a drift summary:

| Metric | What it measures | Threshold |
|---|---|---|
| `unmapped_mention_rate` | Fraction of entity mentions the AI flagged as outside the schema | 15% |
| `vocabulary_miss_rate` | Fraction of Material entities that fell through to LLM resolution | 10% |
| `max_degree` | Maximum number of edges on any single node (hub detection) | 200 |
| `provenance_coverage` | Fraction of edges that have a source quote | 85% |

If any threshold is exceeded, the drift report recommends which schema change would fix it — new vocabulary entries, revised patterns, or a new node type.

---

## Full file

The complete `firecraft.blueprint.yaml` is at `spec/examples/firecraft.blueprint.yaml` in the repo. Use it as the starting point for your own blueprint by copying and modifying it.

[Command reference :octicons-arrow-right-24:](../reference/forge.md){ .md-button .md-button--primary }
