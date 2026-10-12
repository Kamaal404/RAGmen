# Troubleshooting

---

## Installation

??? failure "`forge: command not found`"

    The forge package was not installed, or the wrong Python environment is active.

    ```bash
    cd forge && pip install -e ".[dev]"
    ```

    Verify: `forge --help`

??? failure "`ModuleNotFoundError: No module named 'forge'`"

    Same root cause. If using a virtual environment (`venv`, `conda`), make sure it is activated before installing and running.

??? failure "`pip: command not found`"

    Try: `python -m pip install -e ".[dev]"`

---

## Database

??? failure "`Connection refused: bolt://localhost:7687`"

    Neo4j is not running.

    ```bash
    docker compose up -d
    ```

    Wait ~10 seconds, then retry. Verify Docker is open and running:

    ```bash
    docker ps
    ```

??? failure "`Authentication failure` on Neo4j"

    The credentials in `.env` do not match what Neo4j was initialized with.

    Check your `.env`:

    ```
    NEO4J_USER=neo4j
    NEO4J_PASSWORD=password
    ```

    These must match the values in `docker-compose.yml`. If you changed the password in the Neo4j browser, update `.env` to match.

??? failure "Database is empty after `forge cook`"

    Three possible causes:

    1. **`forge cook` used `--store memory`** — in-memory data disappears when the command exits. Run again with `--store neo4j`.

    2. **Indexes not created** — run `make reset` to wipe and restart the database, then run `forge cook` again.

    3. **Run was interrupted** — run `forge cook src-abc123` again, it will resume from the last completed chunk.

---

## Blueprint

??? failure "`blueprint is invalid: pattern source unknown`"

    A pattern references a node type not declared in `node_types`.

    Example:
    ```
    pattern [Ingredient, USED_IN, Recipe] — source label 'Ingredient' not in node_types
    ```

    Fix: either add `Ingredient` to `node_types`, or correct the pattern.

??? failure "`blueprint is invalid: invalid regex`"

    A `hierarchy` or `skip_regions` pattern is not valid Python regex.

    Test your regex at [regex101.com](https://regex101.com) (Python flavor). In YAML, backslashes need escaping: use `\\d+` not `\d+`.

??? failure "`blueprint not approved`"

    You tried to run `forge add` with an unapproved blueprint.

    Fix: once satisfied with the blueprint, add:

    ```yaml
    approved: true
    approved_by: your-name
    approved_at: "2026-10-12"
    ```

---

## Extraction

??? failure "`openai API key not set`"

    Add your key to `.env`:

    ```
    OPENAI_API_KEY=sk-...
    ```

    For testing without an API key:

    ```bash
    forge extract src-abc123 --provider mock
    ```

??? question "Extraction is very slow"

    Normal rate: 1–3 chunks per second. A 300-page book (~150 chunks) takes 2–5 minutes.

    If stuck for >20 minutes:
    - Check your API key has not hit a rate limit or quota
    - Press `Ctrl+C` to stop, then run `forge cook` again — it resumes from where it left off

??? question "Graph has hub nodes with hundreds of edges"

    This almost always means the schema is too open or entity resolution is not running.

    Checklist:

    - [ ] Blueprint has `closed: true` in the ontology section
    - [ ] All patterns are specific `[SourceType, REL, TargetType]` triples
    - [ ] Run `forge conflicts` to see which entities have the most disagreements

---

## Serving and answers

??? failure "`lattice-serve` starts but `agent ask` returns no results"

    Check that both are using the same store:
    - `forge cook --store neo4j` writes to Neo4j → start serve with `--store neo4j`
    - `forge cook --store memory` data is gone when the command exited → re-cook with `--store neo4j`

??? question "`agent ask` refuses to answer"

    This is intentional. `refuse_outside_corpus: true` in your blueprint means the agent says "I cannot find that in the documents" rather than guessing from general knowledge.

    If the answer *should* be there:

    1. Check the source was extracted: `forge status src-abc123`
    2. Re-run extraction: `forge cook src-abc123`
    3. Rephrase using terms from the document

---

## Getting help

Open an issue at [github.com/Kamaal404/RAGmen/issues](https://github.com/Kamaal404/RAGmen/issues) with:

- The exact command you ran
- The full output / error message
- What you expected to happen
