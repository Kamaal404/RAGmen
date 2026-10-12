# Designing a Schema

This guide walks through the five steps of designing a blueprint schema for a new corpus.

---

## Step 1 — Inspect your corpus

Before writing any YAML, run the inspector on your documents:

```bash
python blueprint/scripts/inspect_corpus.py --input your-document.pdf --show-sample
```

Read the output carefully. It shows you:

- **Heading structure** — which regex patterns would match your document's headings
- **Sample text** — representative paragraphs from different sections
- **Estimated chunk sizes** — how many chunks the document would produce

!!! tip

    Always open the extracted `.txt` file after inspection. If headings did not survive the PDF extraction, that is a cleanup problem — no amount of schema design will fix it. Re-run with a different PDF engine (`--engine pdfplumber`).

---

## Step 2 — List your workload questions

Write down the **ten questions** your users most need answered. Be specific:

| Generic | Specific |
|---|---|
| "Tell me about fires" | "Which fire structures require birch bark?" |
| "What are the hazards?" | "Which procedures carry a carbon monoxide risk?" |
| "Ingredients?" | "What tinder materials work in wet conditions?" |

For each specific question, ask yourself:
- What **types of things** appear in this answer? → those become `node_types`
- What **connects** them? → those become `relationship_types`

---

## Step 3 — Design your node types

Node types are the categories of things in your documents. Start with **3 to 5**.

```yaml
ontology:
  node_types:
    - label: Procedure       # (1)
      description: A fire-building structure or technique
      answers: "What should I build or do?"
      properties:
        - name: name
          type: STRING
          required: true

    - label: Material
      description: A physical material consumed in fire building
      answers: "What do I need?"
      properties:
        - name: name
          type: STRING
          required: true
        - name: role         # (2)
          type: STRING
```

1. Labels must be PascalCase with no spaces.
2. Properties are optional fields the AI extracts per entity. Keep them to 1–2 per type.

!!! warning "Keep it small"

    A schema with 20 node types is almost always wrong. It produces a hairball where everything is connected to everything and retrieval degrades to random.

    Rule of thumb: if you cannot write a one-sentence `description` for a node type, merge it with another or split it more carefully.

### Properties: what to extract per entity

| Use properties for... | Avoid properties for... |
|---|---|
| Factual attributes (role, unit, date) | Free-text descriptions (just use the chunk text) |
| Hard-filter values (jurisdiction, allergen) | Things that would go in another node type |

---

## Step 4 — Design your relationships

Relationship types connect node types. The key constraint is **domain and range** — which node types can be source and target.

```yaml
  relationship_types:
    - label: REQUIRES_MATERIAL
      description: This procedure or technique needs this material

    - label: HAS_HAZARD
      description: This procedure carries this specific hazard

  patterns:              # (1)
    - [Procedure, REQUIRES_MATERIAL, Material]
    - [Technique, REQUIRES_MATERIAL, Material]
    - [Procedure, HAS_HAZARD, Hazard]
```

1. **Patterns are the closed list of allowed connections.** The AI can only create relationships that match one of these exactly. This is what prevents hallucinated edges.

!!! tip "Naming conventions"

    - Relationship labels are `SCREAMING_SNAKE_CASE`
    - Name them as verbs or verb phrases: `REQUIRES_MATERIAL`, `HAS_HAZARD`, `SUPERSEDES`, `DEFINED_IN`
    - Avoid generic names like `RELATED_TO` or `HAS` — they tell the retriever nothing

---

## Step 5 — Design the structure section

Tell the chunker how your document is organized:

```yaml
structure:
  hierarchy:
    - level: 1
      label: Chapter
      pattern: '^Chapter\s+\d+'    # (1)
    - level: 2
      label: Section
      pattern: '^\d+\.\d+\s+'
  skip_regions:                    # (2)
    - label: TableOfContents
      start_pattern: '^Table of Contents'
      end_pattern: '^Chapter\s+1'
    - label: Index
      start_pattern: '^Index$'
      end_pattern: null
```

1. Patterns are Python regex. Test yours at [regex101.com](https://regex101.com) — Python flavor.
2. Skip regions are ignored during chunking. They would cost tokens to process and produce nothing useful.

**Chunk on structural boundaries, not fixed character counts.** A legal article is a chunk. A recipe is a chunk. A chapter section is a chunk. Fixed-size splitting cuts relationships in half and those are exactly the relationships you need to extract.

---

## Step 6 — Add guardrails

```yaml
guardrails:
  conflict_policy: surface_both    # (1)
  hard_filters:
    - name: hazards_always
      mode: force_include
      labels: [Hazard]
      rationale: Safety hazards must always surface.
  answer_policy:
    require_citation: true         # (2)
    refuse_outside_corpus: true    # (3)
```

1. `surface_both` — when two sources disagree, show both. `last_write_wins` picks the most recent.
2. Every claim in an answer must be backed by a retrievable chunk.
3. The agent refuses to answer questions it cannot find in the corpus.

!!! info "Hard filters vs soft filters"

    Hard filters are enforced in Cypher `WHERE` clauses — they cannot be overridden by a user prompt. Soft filters are suggestions in the system prompt that the model can ignore.

    Anything safety-critical (allergens, hazards, access permissions) should be a hard filter.

---

## Step 7 — Validate and approve

```bash
python blueprint/scripts/validate_blueprint.py my-blueprint.yaml
```

Fix any errors reported, then add the approval block:

```yaml
approved: true
approved_by: your-name
approved_at: "2026-10-12"
```

Forge will refuse to process documents without an approved blueprint. This is intentional — it forces a conscious sign-off before extraction spend.

---

## Common mistakes

| Mistake | What goes wrong | Fix |
|---|---|---|
| `closed: false` or missing | AI invents thousands of labels | Add `closed: true` |
| Too many node types (>8) | Hub nodes, poor retrieval | Merge types that answer the same question |
| Patterns too permissive | Hallucinated edges everywhere | Make every pattern specific: `[A, REL, B]` |
| No `skip_regions` | Index and TOC entries extracted as entities | Add patterns for front matter and indexes |
| Generic relationship names (`HAS`, `RELATED_TO`) | Retriever cannot distinguish edges | Use specific verb phrases |

---

[See a complete working example :octicons-arrow-right-24:](reference.md){ .md-button .md-button--primary }
