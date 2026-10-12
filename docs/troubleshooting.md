# Troubleshooting

Common errors and how to fix them.

---

## Installation problems

### `forge: command not found`

The forge package was not installed, or it was installed in a different Python environment.

**Fix:**
```
cd forge && pip install -e ".[dev]"
```

Then verify with `forge --help`.

---

### `ModuleNotFoundError: No module named 'forge'`

Same as above — the package is not installed in the current environment.

If you are using a virtual environment (`venv`, `conda`, etc.), make sure it is activated before installing and before running commands.

---

### `pip: command not found`

Python is installed but `pip` is not on your PATH.

Try:
```
python -m pip install -e ".[dev]"
```

---

## Database problems

### `neo4j driver not installed`

You tried to use `--store neo4j` without the Neo4j Python driver.

**Fix:**
```
pip install neo4j
```

---

### `Connection refused: bolt://localhost:7687`

The Neo4j container is not running.

**Fix:**
```
docker compose up -d
```

Wait about 10 seconds for Neo4j to start, then try again.

If it still fails, check whether Docker is running:
```
docker ps
```

---

### `Authentication failure` on Neo4j

The username or password in your `.env` file does not match what Neo4j was set up with.

**Fix:** Check your `.env` file:
```
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

Make sure these match the values in `docker-compose.yml`. If you changed the password in the Neo4j browser, update `.env` to match.

---

### Database is empty after `forge cook`

A few possible causes:

1. **`forge cook` ran with `--store memory`** — in-memory data disappears when the command exits. Run again with `--store neo4j`.

2. **Neo4j indexes were not created** — this can cause silent failures. Run `make reset` to wipe and restart the database, then run `forge cook` again.

3. **`forge cook` was interrupted** — run it again, it will resume from the last completed chunk.

---

## Blueprint problems

### `blueprint is invalid: pattern source unknown`

A pattern in your blueprint references a node type that is not declared in `node_types`.

**Example error:**
```
pattern [Ingredient, USED_IN, Recipe] — source label 'Ingredient' not in node_types
```

**Fix:** Either add `Ingredient` to your `node_types` list, or correct the pattern to use an existing node type.

---

### `blueprint is invalid: invalid regex`

A pattern in `hierarchy` or `skip_regions` is not a valid regular expression.

**Fix:** Test your regex at [regex101.com](https://regex101.com) before putting it in the blueprint. Remember to escape backslashes in YAML (use `\\d+` not `\d+`).

---

### `blueprint not approved`

You tried to run `forge add` with a blueprint that has `approved: false` or no approval block.

**Fix:** Once you are satisfied with the blueprint, add:
```yaml
approved: true
approved_by: your-name
approved_at: "2026-10-12"
```

---

## Extraction problems

### `openai API key not set`

You ran `forge cook` or `forge extract` without an API key.

**Fix:** Add your key to `.env`:
```
OPENAI_API_KEY=sk-...
```

Or use the mock provider for testing (no API key needed):
```
forge extract src-abc123 --provider mock
```

---

### Extraction is very slow

Normal extraction rate is roughly 1-3 chunks per second depending on chunk size and API latency. A 300-page book with 150 chunks takes 2-5 minutes.

If it has been running for more than 20 minutes with no progress, check:
- Your API key has not hit its rate limit or quota
- The terminal is not frozen (press Enter to see if it responds)

If a run is truly stuck, press `Ctrl+C` to stop it. Run `forge cook` again — it resumes from the last completed chunk automatically.

---

### Graph has too many entities / hub nodes

This usually means the schema is too open or entity resolution is not working.

Signs of the problem:
- One entity has hundreds of relationships
- Entity names are all variations of the same thing ("fire", "the fire", "a fire")
- `forge conflicts` shows thousands of conflicts

**Fixes:**
1. Make sure your blueprint has `closed: true` in the ontology section
2. Check that `patterns` is restrictive — every allowed `[source, REL, target]` combination should make sense
3. Run `forge conflicts` to see which entities have the most disagreements

---

## Serving problems

### `lattice-serve` starts but `agent ask` gets no results

Make sure both commands are using the same store:
- If `lattice-serve --store memory` is running, the data lives in that process only
- If you ran `forge cook --store neo4j`, start serve with `--store neo4j`

---

### `agent ask` refuses to answer

This is intentional behavior, not an error. The agent is configured with `refuse_outside_corpus: true` in the blueprint's `guardrails` section. If the answer cannot be found in the documents, it says so.

If you believe the answer *should* be in the documents, check:
1. Was the source extracted? Run `forge status src-abc123` to check
2. Did extraction find anything? Run `forge cook src-abc123` again and check the entity count in the output
3. Is the question using the right vocabulary? Try rephrasing using terms from the document

---

## Getting more help

Open an issue at [github.com/Kamaal404/RAGmen/issues](https://github.com/Kamaal404/RAGmen/issues) with:
- What command you ran
- What output you got
- What you expected instead
