# `agent` — Command Reference

The Agent is the answer policy layer. It calls Serve's retrieval tools, formats the results, enforces citation requirements, and surfaces conflicting sources.

---

## `agent ask`

Ask a single question.

```bash
agent ask "how do I build a dakota fire hole?"
agent ask "which fire structures are suited for cooking?"
agent ask "what tinder works in wet conditions?"
```

**What happens:**

1. Calls `serve.search` with your query
2. Expands any high-scoring entities with `serve.expand`
3. Verifies each claim with `serve.cite`
4. Formats the answer with inline citations
5. If conflicts exist, surfaces both sides with attribution
6. If the answer is not in the corpus, refuses to answer

---

## `agent interactive`

Opens a question loop so you can ask multiple questions without restarting.

```bash
agent interactive
```

```
RAGmen › how do I build a dakota fire hole?
...answer...

RAGmen › what about in wet conditions?
...answer...

RAGmen › quit
```

---

## Answer policies

The agent enforces the policies declared in your blueprint's `answer_policy` section:

| Policy | What it does |
|---|---|
| `require_citation: true` | Every factual claim in the answer must be backed by a retrievable chunk. Claims without a source are dropped. |
| `refuse_outside_corpus: true` | If the answer cannot be found in the indexed documents, the agent says so instead of drawing on general AI knowledge. |

These are not suggestions — they are structural constraints on how answers are assembled.

---

## Conflict surfacing

When `conflict_policy: surface_both` is set in the blueprint:

```
Question: what is the best tinder material?

Source A (manual.pdf, p.12):
  Dry grass is the most readily available tinder in temperate climates.
  [Chapter 1 / Tinder]

Source B (guide.pdf, p.7):
  Dry grass fails in wet conditions. Birch bark is more reliable.
  [Chapter 2 / Material Selection]

These sources disagree. Both are shown as recorded.
```

When `conflict_policy: prefer_strictest` is set (appropriate for safety-critical corpora), the more cautious assertion is used and the conflict is noted.

---

## Citation format

Every answer includes citations in the form:

```
[1] Chapter 2 / Dakota Fire Hole (manual.pdf, p.81)
[2] Chapter 3 / Ventilation (manual.pdf, p.94)
```

Each citation number corresponds to a specific chunk. Run `serve cite <chunk_id>` to retrieve the verbatim text from that chunk.
