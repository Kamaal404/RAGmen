# Blueprint design

<div align="center">
<img src="../assets/originals/ragmen-recipe.png" width="160"/>
</div>

A blueprint is the design document that tells RAGmen what to look for in your corpus. It answers three questions:

1. What *kinds of things* exist in your documents?
2. What *relationships* exist between those things?
3. What *questions* do users need to answer?

You create a blueprint once per corpus type. Once approved, Forge and Serve execute it exactly as written — they never change it or make decisions about what things mean.

---

## Why you design before you extract

Designing the schema before extraction is the single most important practice in RAGmen. Here is why:

- Extraction calls the AI on every chunk of your document. A 300-page book with 150 chunks costs real money.
- If your schema is wrong — too broad, missing a node type, using the wrong relationship — you discover this when asking questions, weeks after you ran the extraction.
- Fixing the schema means re-running extraction from scratch.

The blueprint is the cheap part. Get it right first, then spend on extraction.

---

## Anatomy of a blueprint

Open `spec/examples/firecraft.blueprint.yaml` to see a complete working example. Here is what the key sections mean:

### `blueprint_version` and `name`

```yaml
blueprint_version: "1.0.0"
name: firecraft
description: Fire building and survival techniques
```

The version and name identify this blueprint. Forge stamps every extracted chunk with the blueprint's hash so you can always trace what schema was used.

### `structure` — how to split your document

```yaml
structure:
  hierarchy:
    - level: 1
      label: Chapter
      pattern: '^Chapter\s+\d+'
    - level: 2
      label: Section
      pattern: '^\d+\.\d+\s+'
  skip_regions:
    - label: TableOfContents
      start_pattern: '^Table of Contents'
      end_pattern: '^Chapter\s+1'
```

`hierarchy` tells the chunker where document boundaries are. Each level is a regex pattern that matches headings in your document.

`skip_regions` tells the chunker to ignore certain sections entirely — table of contents, indexes, copyright pages. These sections cost tokens to extract and produce nothing useful.

### `ontology` — the schema

```yaml
ontology:
  closed: true
  node_types:
    - label: Procedure
      description: A fire-building structure or technique
      answers: "What should I build or do?"
      properties:
        - name: name
          type: STRING
          required: true

    - label: Material
      description: A physical material used in fire building
      answers: "What do I need?"
      properties:
        - name: name
          type: STRING
          required: true
        - name: role
          type: STRING

  relationship_types:
    - label: REQUIRES_MATERIAL
      description: This procedure needs this material

  patterns:
    - [Procedure, REQUIRES_MATERIAL, Material]
    - [Technique, REQUIRES_MATERIAL, Material]
```

**`closed: true`** is important. It means the AI is not allowed to invent new node types or relationship types outside this schema. Without it, the AI creates thousands of inconsistent labels and the graph becomes unusable.

**`node_types`** — the categories of things in your documents. Each one has:
- `label` — the internal name (PascalCase, no spaces)
- `description` — what this type means (used in the AI prompt)
- `answers` — what user question this node type helps answer (used in retrieval)
- `properties` — fields to extract for each entity

**`relationship_types`** — the types of connections between entities. Keep these to a small, meaningful set.

**`patterns`** — the allowed combinations of `[source_type, RELATIONSHIP, target_type]`. The AI is only allowed to create relationships that match one of these patterns. This prevents hallucinated connections.

### `guardrails`

```yaml
guardrails:
  conflict_policy: surface_both
  hard_filters:
    - name: hazards_always
      mode: force_include
      labels: [Hazard]
      rationale: Safety hazards must always appear in answers.
  answer_policy:
    require_citation: true
    refuse_outside_corpus: true
```

`conflict_policy: surface_both` — when two documents disagree, show both answers with attribution instead of picking one.

`hard_filters` — rules that always apply regardless of the question. The example forces Hazard nodes to always appear in answers — a safety requirement.

`answer_policy` — `require_citation: true` means every claim must be backed by a chunk. `refuse_outside_corpus: true` means the agent will not use general AI knowledge.

---

## Designing a blueprint for your documents

### Step 1 — Inspect your corpus

Before opening a YAML file, run the inspector on a sample of your documents:

```
python blueprint/scripts/inspect_corpus.py --input your-document.pdf --show-sample
```

This shows you:
- How headings are structured (which regex patterns would match them)
- Sample text from different sections
- Estimated chunk sizes

Read the output before designing anything.

### Step 2 — Answer the workload questions

Write down the ten questions your users most need answered. For each question:
- What types of entities would appear in the answer?
- What relationship connects them?

This exercise naturally produces your `node_types` and `relationship_types`.

### Step 3 — Keep the schema small

Start with 3 to 5 node types and 3 to 5 relationship types. You can always add more later by revising the blueprint. A schema with 20 node types and 30 relationship types is almost always wrong — it produces a hairball that retrieves worse than plain search.

Rules of thumb:
- If you cannot write a one-sentence description for a node type, split or merge it
- If a relationship pattern has more than 3 allowed `[source, REL, target]` combinations, it is probably too generic
- Every node type should answer at least one of your ten workload questions

### Step 4 — Validate the blueprint

```
python blueprint/scripts/validate_blueprint.py your-blueprint.yaml
```

Fix any errors it reports. Common ones:
- A pattern references a node type not in `node_types`
- A relationship type is used in patterns but not declared in `relationship_types`
- A regex pattern in `hierarchy` is invalid

### Step 5 — Approve and stamp

Once the validator passes, add the approval block:

```yaml
approved: true
approved_by: your-name
approved_at: "2026-10-12"
```

Forge will refuse to run on an unapproved blueprint. This is intentional — it forces a conscious sign-off before any extraction spend.

---

## Revising a blueprint

When you need to change the schema after extraction has already run:

1. Edit the blueprint YAML
2. Run the validator: `python blueprint/scripts/validate_blueprint.py your-blueprint.yaml`
3. Run `forge cook <source_id>` again — Forge detects which chunks were extracted under the old blueprint hash and re-extracts only those

You do not need to retract and re-add your sources. Forge handles targeted re-extraction automatically.

---

## Next step

[Command reference](commands.md) — every command explained with examples.
