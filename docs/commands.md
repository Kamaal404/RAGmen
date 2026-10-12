# Command reference

Every command RAGmen provides, with examples.

---

## `forge` — build-time commands

Forge is the tool that processes your documents and builds the knowledge graph. Run `forge --help` at any time to see available commands.

---

### `forge add`

Register a document and split it into chunks.

```
forge add <path-to-file> --blueprint <path-to-blueprint.yaml>
```

**Options:**

| Option | What it does | Default |
|---|---|---|
| `--blueprint` | Path to the blueprint YAML (required) | — |
| `--owner` | Who is adding this source | (empty) |
| `--visibility` | `public` or `private` | `public` |

**Examples:**

```bash
# Add a PDF
forge add manual.pdf --blueprint spec/examples/firecraft.blueprint.yaml

# Add a text file with owner info
forge add policies.txt --blueprint my-blueprint.yaml --owner alice

# Add a markdown file
forge add guide.md --blueprint my-blueprint.yaml
```

**What happens:**
- The file is read and converted to clean text
- Text is split into chunks at structural boundaries (chapters, sections)
- Each chunk gets a unique fingerprint based on its content
- The source is registered in `forge.db`

**Running it again on an unchanged file:**
```
unchanged, 0 new chunks  (src-abc123)
```
RAGmen compares the file's fingerprint and skips processing entirely. No cost.

---

### `forge list`

Show all registered sources.

```
forge list
forge list --status ready
forge list --status chunking
```

**Status values:**

| Status | Meaning |
|---|---|
| `chunking` | Currently being processed |
| `ready` | Chunked, waiting for extraction |
| `extracted` | Extraction complete |
| `retracted` | Removed from the graph |

---

### `forge status`

Show details for one source.

```
forge status src-abc123
```

Shows the file path, content hash, number of chunks, blueprint used, and current status.

---

### `forge chunks`

Show the chunks for a source.

```
forge chunks src-abc123
forge chunks src-abc123 --json
```

Each chunk shows its path (breadcrumb in the document hierarchy), page number, character range, and text preview.

---

### `forge extract`

Run extraction on an already-registered source.

```
forge extract src-abc123
forge extract src-abc123 --dry-run
forge extract src-abc123 --dry-run --dry-run-dir my-prompts/
```

**Options:**

| Option | What it does |
|---|---|
| `--dry-run` | Write prompts to disk, do not call the AI |
| `--dry-run-dir` | Where to write the dry-run prompts (default: `dry-run/`) |
| `--provider` | `mock` (for testing) or `openai` (default) |

The dry run is useful for:
- Estimating cost before committing to a full extraction
- Inspecting what the AI will be asked to do
- Debugging schema issues without spending money

---

### `forge cook`

Run the full pipeline on a source: extract → resolve → embed → write to graph.

```
forge cook src-abc123
forge cook src-abc123 --store neo4j
forge cook src-abc123 --store memory
```

**Options:**

| Option | What it does | Default |
|---|---|---|
| `--store` | `neo4j` or `memory` | `neo4j` |

Use `--store memory` for testing — data lives in RAM and is gone when the command exits. Use `--store neo4j` for production.

**What happens:**
1. Calls the AI on each unprocessed chunk
2. Extracts entities and relationships per the blueprint schema
3. Resolves duplicate entity names (fuzzy matching)
4. Embeds all entities and chunks as vectors
5. Writes everything to the graph
6. Reports drift (unmapped mentions, hub violations)

If the run is interrupted at any point, run it again — it resumes from the last completed chunk.

---

### `forge update`

Re-run extraction on a source where the file has changed.

```
forge update src-abc123
```

Forge compares the new version of the document against the stored chunks. Only changed chunks are re-extracted. Unchanged chunks are skipped.

---

### `forge retract`

Remove a source and all its data from the graph.

```
forge retract src-abc123
```

**What happens:**
- All chunks for this source are removed
- All entities that were *only* in this source are removed
- Entities shared with other sources are left intact (refcounting)
- The source record is marked `retracted`

This is safe to run at any time. It will not break other sources.

---

### `forge conflicts`

List cases where two or more sources disagree about the same fact.

```
forge conflicts
```

Example output:

```
CONFLICT: Procedure "bow drill" — REQUIRES_MATERIAL — Material "dry grass"
  Asserted by: src-abc123 (manual.pdf, p. 12)
  Asserted by: src-def456 (guide.pdf, p. 7)  [relation: NOT_RECOMMENDED]

1 conflict found
```

Conflicts are surfaced in answers automatically — you do not need to run this manually for users to see them.

---

### `forge blueprint`

Validate a blueprint file without adding any sources.

```
forge blueprint my-blueprint.yaml
forge blueprint my-blueprint.yaml --check
```

Equivalent to running the validator script directly.

---

## `lattice-serve` — retrieval server

Starts the MCP retrieval server that answers queries against the graph.

```
lattice-serve --store memory --blueprint spec/examples/firecraft.blueprint.yaml
lattice-serve --store neo4j --blueprint my-blueprint.yaml
lattice-serve --store neo4j --neo4j-uri bolt://localhost:7687 --neo4j-user neo4j --neo4j-password password
```

**Options:**

| Option | What it does | Default |
|---|---|---|
| `--store` | `neo4j` or `memory` | `memory` |
| `--blueprint` | Blueprint YAML to load guardrails from | (none) |
| `--neo4j-uri` | Neo4j connection URI | `bolt://localhost:7687` |
| `--neo4j-user` | Neo4j username | `neo4j` |
| `--neo4j-password` | Neo4j password | (empty) |

The server exposes five tools over the MCP protocol:

| Tool | What it does |
|---|---|
| `search` | Hybrid search: vector + keyword + graph |
| `expand` | Explore the neighbourhood of a chunk or entity |
| `cite` | Get verbatim text + source + page for a chunk |
| `explain_path` | Show how two entities are connected |
| `schema` | Return the active blueprint ontology |

Leave this running in a terminal while using `agent ask`.

---

## `agent ask` — question answering

Ask a question against the running serve server.

```
agent ask "your question here"
agent interactive
```

**`agent ask`** — one question, one answer with citations.

**`agent interactive`** — opens a loop where you can ask multiple questions without restarting.

**Answer policies enforced:**
- Every claim in the answer is backed by a chunk with source + page
- If the answer cannot be found in the corpus, the agent says so instead of guessing
- If two sources disagree, both sides are shown with attribution

---

## Blueprint scripts

These run without any AI or database — they only need Python.

### Validate a blueprint

```
python blueprint/scripts/validate_blueprint.py my-blueprint.yaml
python blueprint/scripts/validate_blueprint.py my-blueprint.yaml --hash
```

`--hash` also prints the content hash, which is stamped onto every extracted chunk.

### Inspect a corpus

```
python blueprint/scripts/inspect_corpus.py --input document.pdf --show-sample
python blueprint/scripts/inspect_corpus.py --input corpus-folder/ --show-sample
```

Use this before designing a blueprint to understand the document structure.

---

## Docker commands (for the Neo4j database)

```bash
make up       # start Neo4j
make down     # stop Neo4j
make shell    # open a Cypher shell inside Neo4j
make reset    # wipe the database and restart fresh
```

Or directly with Docker:

```bash
docker compose up -d    # start
docker compose down     # stop
docker compose down -v  # stop and delete all data
```
