# `forge` — Command Reference

Forge is the build-time tool. It registers documents, chunks them, extracts knowledge, and writes the graph. Run `forge --help` at any time.

---

## `forge add`

Register a document and split it into chunks.

```bash
forge add <path> --blueprint <blueprint.yaml> [options]
```

**Arguments:**

| Argument | Required | Description |
|---|:---:|---|
| `<path>` | Yes | Path to the file (PDF, Markdown, TXT, HTML) |
| `--blueprint` | Yes | Path to the approved blueprint YAML |
| `--owner` | No | Who is adding this source (stored in registry) |
| `--visibility` | No | `public` or `private` (default: `public`) |

**Example:**

```bash
forge add manual.pdf --blueprint spec/examples/firecraft.blueprint.yaml --owner alice
```

**What it does:**

1. Converts the file to clean text (PDF: fixes headers, hyphenation, reflow)
2. Splits text into chunks at structural boundaries from `blueprint.structure`
3. Applies `skip_regions` to exclude front matter, indexes, etc.
4. Assigns a content-addressed ID to each chunk: `hash(source_id + path + text)`
5. Registers the source and chunks in `forge.db`

!!! success "Idempotent by design"

    Running `forge add` on an unchanged file outputs:

    ```
    unchanged, 0 new chunks  (src-abc123)
    ```

    RAGmen compares the file's SHA-256 fingerprint and skips everything if nothing changed. Zero AI calls, zero cost.

---

## `forge list`

Show all registered sources.

```bash
forge list
forge list --status ready
```

**Status values:**

| Value | Meaning |
|---|---|
| `chunking` | Being processed |
| `ready` | Chunked, waiting for extraction |
| `extracted` | Extraction complete |
| `retracted` | Removed |

---

## `forge status`

Show details for one source.

```bash
forge status src-abc123
```

Output includes: file path, content hash, chunk count, blueprint used, current status, added by, added at.

---

## `forge chunks`

Show the chunks for a source.

```bash
forge chunks src-abc123
forge chunks src-abc123 --json
```

Each chunk shows its breadcrumb path in the document hierarchy, page number, character range, and text preview.

---

## `forge extract`

Run extraction on a registered source. Sends chunks to the AI and extracts entities + relationships per the blueprint schema.

```bash
forge extract src-abc123
forge extract src-abc123 --dry-run
forge extract src-abc123 --provider mock
```

**Options:**

| Option | Description |
|---|---|
| `--dry-run` | Write prompts to disk without calling the AI |
| `--dry-run-dir <dir>` | Where to write dry-run output (default: `dry-run/`) |
| `--provider mock` | Use a mock provider that returns fake data (no API key needed) |
| `--provider openai` | Use OpenAI (default) |

!!! tip "Always dry-run first on a new blueprint"

    The dry-run shows exactly what the AI will receive — your schema plus a chunk of text. Check that the schema looks right before spending on a full extraction run.

---

## `forge cook`

Run the complete pipeline: extract → resolve → embed → write to graph.

```bash
forge cook src-abc123
forge cook src-abc123 --store neo4j
forge cook src-abc123 --store memory
```

**Options:**

| Option | Description | Default |
|---|---|---|
| `--store neo4j` | Write to the running Neo4j instance | `neo4j` |
| `--store memory` | Write to an in-process dict (lost when command exits) | — |

**Stages:**

```
extract   → AI reads chunks, outputs typed entities and relationships
resolve   → Deduplication: "bow drill" + "bow-drill" → one node
embed     → Vectors computed for all entities and chunks
write     → Everything loaded into the graph store
report    → Drift: unmapped mentions, hub violations, coverage
```

!!! info "Kill-safe by design"

    Every chunk has a per-chunk status in the job ledger. If `forge cook` is interrupted — by `Ctrl+C`, a crash, or a power cut — run the same command again. It resumes from the last completed chunk. You will never pay twice for the same chunk.

---

## `forge update`

Re-run extraction on a source where the file has changed.

```bash
forge update src-abc123
```

Forge compares the new version against the stored chunks. Only changed chunks are re-extracted. Unchanged chunks are skipped.

---

## `forge retract`

Remove a source and all its data from the graph.

```bash
forge retract src-abc123
```

**What gets removed:**

- All chunks for this source
- All entities that are *only* referenced by this source
- Embeddings for removed entities and chunks

**What is preserved:**

- Entities shared with other sources (refcounting protects them)
- All data from other sources

This is safe to run at any time without affecting other sources.

---

## `forge conflicts`

List cases where two or more sources make contradictory assertions.

```bash
forge conflicts
```

Example output:

```
CONFLICT: Procedure "bow drill" — REQUIRES_MATERIAL — Material "dry grass"
  Asserted by: src-abc123 (manual.pdf p.12): "dry grass is preferred"
  Asserted by: src-def456 (guide.pdf  p.7):  "dry grass does not work in wet conditions"

1 conflict found
```

Conflicts are surfaced in answers automatically when `conflict_policy: surface_both` is set in the blueprint.

---

## `forge blueprint`

Validate a blueprint file without adding any sources.

```bash
forge blueprint my-blueprint.yaml
forge blueprint my-blueprint.yaml --check
```

Equivalent to running `python blueprint/scripts/validate_blueprint.py` directly.
