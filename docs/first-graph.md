# Your first graph

<div align="center">
<img src="../assets/originals/ragmen-kitchen.png" width="160"/>
</div>

This guide walks you through building a knowledge graph from a PDF and asking your first question. We use the example document that comes with RAGmen — a fire-building manual — so you can follow along without needing your own document yet.

---

## Before you start

Make sure you have completed [Installation](installation.md) and that:

- `forge --help` works in your terminal
- `docker compose up -d` is running (check with `docker ps`)

---

## Overview of what you are about to do

```
1. Validate the blueprint   (the design that tells RAGmen what to look for)
2. Register the document    (forge add)
3. Extract the knowledge    (forge cook)
4. Start the server         (lattice-serve)
5. Ask a question           (agent ask)
```

Each step takes a few seconds except step 3, which calls the AI and may take a few minutes depending on document size.

---

## Step 1 — Validate the blueprint

A blueprint is a YAML file that describes what RAGmen should look for in your documents. The repo comes with an example blueprint for the fire-building manual:

```
python blueprint/scripts/validate_blueprint.py spec/examples/firecraft.blueprint.yaml
```

You should see: `blueprint is valid`.

This step costs nothing — no AI, no database. It just checks that the blueprint file is correctly written.

---

## Step 2 — Register the document

```
forge add "The Complete Book of Fire Building Campfires for Warmth Light Cooking and Survival.pdf" \
  --blueprint spec/examples/firecraft.blueprint.yaml
```

What this does:
- Reads the PDF and converts it to clean text
- Splits the text into chunks based on the document's structure (chapters, sections)
- Registers the document in a local database (`forge.db`)
- Stamps every chunk with a fingerprint so unchanged content is never reprocessed

You will see output like:

```
source registered: src-abc123
chunked: 147 chunks
```

Take note of the source ID (the `src-abc123` part) — you will use it in the next step.

**Running it a second time on the same file:**

```
unchanged, 0 new chunks  (src-abc123)
```

RAGmen detects that the file has not changed and skips it entirely. No AI calls, no cost.

---

## Step 3 — Extract the knowledge

This is where the AI reads your chunks and builds the graph. Before spending any money, do a dry run first to see what would be extracted:

```
forge extract src-abc123 --dry-run
```

This writes the prompts to a folder called `dry-run/` so you can inspect them. No AI calls are made.

When you are ready, run the full pipeline:

```
forge cook src-abc123
```

What this does (in order):
1. Sends each chunk to the AI with your blueprint's schema
2. Extracts named entities (Procedures, Materials, Techniques, Hazards)
3. Extracts relationships between them (REQUIRES_MATERIAL, HAS_HAZARD)
4. Resolves duplicate entity names (e.g. "bow drill" and "bow-drill" become one)
5. Embeds everything as vectors for similarity search
6. Writes the graph to Neo4j

You will see progress output as each chunk is processed. If the run is interrupted, run the same command again — it resumes from where it stopped.

When it finishes you will see a summary:

```
entities written: 84
relations written: 127
chunks processed: 147
drift: 3 unmapped mentions, 0 hub violations
```

---

## Step 4 — Start the retrieval server

```
lattice-serve --store neo4j --blueprint spec/examples/firecraft.blueprint.yaml
```

The server starts and listens for questions. Leave this terminal open.

For a quick test without Neo4j (in-memory, data is lost when server stops):

```
lattice-serve --store memory --blueprint spec/examples/firecraft.blueprint.yaml
```

---

## Step 5 — Ask a question

Open a second terminal and run:

```
agent ask "how do I build a dakota fire hole?"
```

You will get a response like:

```
The Dakota fire hole is a wind-resistant fire structure built by digging two
interconnected holes in the ground. The first hole holds the fire; the second
is a ventilation tunnel that feeds air to the base of the fire.

Materials required: dry grass (tinder), birch bark (kindling), hardwood (fuel)
Hazards: carbon monoxide in enclosed spaces

Sources:
  [1] Chapter 2 / Dakota Fire Hole (p. 81)
  [2] Chapter 3 / Ventilation (p. 94)
```

Every claim is backed by a source and page number. The agent will not invent information that is not in the document.

---

## What to try next

**Ask about something that has conflicting information:**
```
agent ask "what is the best tinder material?"
```
If multiple sections disagree, RAGmen surfaces both answers with their sources rather than picking one.

**List all sources in the registry:**
```
forge list
```

**See the chunks for a source:**
```
forge chunks src-abc123
```

**Remove a source (and its graph data):**
```
forge retract src-abc123
```
Entities shared with other sources are preserved. Only entities unique to this source are removed.

---

## Next step

[Blueprint design](blueprint.md) — learn how to create a blueprint for your own documents.
