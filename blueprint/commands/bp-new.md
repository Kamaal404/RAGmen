---
description: Design a new blueprint for a corpus, from inspection through sign-off
argument-hint: <path to corpus dir or file>
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

Design a blueprint for: **$ARGUMENTS**

Follow the sequence in `blueprint/SKILL.md`. Run everything from the repo root.
Do not skip ahead to the ontology.

1. `python blueprint/scripts/inspect_corpus.py --input <path> --show-sample`
   Read the output and read real text. If there is no text layer, or the
   sources are docx or epub, stop and say so.

2. Ask the user for 5 to 10 real questions the graph must answer, in their own
   words. If none need more than one hop, say plainly that a vector store is the
   better tool. That is a legitimate outcome.

3. Copy the closest example from `spec/examples/` to
   `blueprints/<name>.blueprint.yaml`. Read `blueprint/references/ontology_design.md`
   and `blueprint/references/forge_contract.md`, then propose types derived from
   the questions, naming for each the question it answers.

4. Vocabularies (`vocab/<label>.csv`), embedding strategy, guardrails, then the
   golden set (`eval/<name>.jsonl`).

5. `python blueprint/scripts/validate_blueprint.py blueprints/<name>.blueprint.yaml --hash`
   Fix every error. Address every warning or say why you accept it.

6. Prove it chunks: `forge --store .blueprint-check/forge.db add <sample> --blueprint <bp> --provider mock`,
   then `forge --store .blueprint-check/forge.db chunks <source_id>`. Check the
   count, the paths, and that no contents or index pages became chunks. Delete
   `.blueprint-check/` afterwards.

7. Present the design and **stop for sign-off**. Do not begin extraction, and do
   not offer to in the same message. Hand over the Forge commands and let the
   user decide when to spend.
