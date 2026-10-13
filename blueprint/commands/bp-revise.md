---
description: Revise an existing blueprint against evidence from a build or a drift report
argument-hint: <blueprint path> [drift report or symptom]
allowed-tools: Read, Write, Edit, Bash, Grep
---

Revise: **$ARGUMENTS**

Revision needs evidence. Ask for the drift report, validation output, or failing
golden-set items before changing anything. Editing an ontology on a hunch is how
blueprints accumulate types nobody can justify.

Map the symptom to a cause:

- **hairball, or undeclared labels in the store** → Forge drops undeclared
  labels and patterns before writing, so these should not exist. Check the
  graph was built by a current Forge, then check the pattern list covers every
  relationship.
- **high pattern violation rate, or "Dropped: N relations" on cook** → the
  extractor keeps proposing an edge the blueprint forbids. Read the violations:
  sometimes the model is right and a pattern is missing (often a reversed
  direction), sometimes a relationship description is ambiguous. Either fix
  is a blueprint change, never a Forge one.
- **duplicate entities** → resolution strategy or a missing vocabulary, not the
  ontology. Add the duplicated surface forms to the CSV's `aliases` column;
  Forge resolves only on exact and fuzzy match.
- **high unmapped mention rate** → the corpus has real concepts the ontology
  lacks. This is the case where adding a type is justified.
- **hub nodes** → resolution failure, or a legitimately central concept that
  belongs in `drift.max_degree_exempt`. Check which before acting.
- **retrieval misses obvious answers** → usually the workload and the embedding
  targets, not the node types.
- **the graph loses to the vector baseline on lookup** → the traversal is
  over-expanding. That is Serve's problem, not the blueprint's.

Bump `blueprint_version` correctly: major for changes that require re-extraction,
minor for additive types, patch for descriptions and non-semantic edits. Then:

```bash
python blueprint/scripts/validate_blueprint.py <blueprint> --hash
```

If `structure` changed, prove it again on a sample with the scratch-registry
check in SKILL.md step 8. Say explicitly which already-ingested types are
invalidated, because Forge uses the stamped hash to decide what to re-extract.
