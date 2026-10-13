# What Forge can execute

The spec says what a blueprint may declare. This page says what Forge does with
it today. Design against this page. A blueprint that is spec-valid but outside
it either fails at build time, unattended and after money is spent, or quietly
does less than it says.

`validate_blueprint.py` enforces every rule here (the "forge contract" section of
the script). Errors mean Forge will fail. Warnings mean Forge will run but
silently do less than the blueprint says. When Forge gains a capability, this
page, the validator and `tests/test_blueprint.sh` change in the same commit.

## Paths

Every relative path in a blueprint (`workload.golden_set`,
`ontology.vocabularies.*.source`) resolves against the **working directory**,
not the blueprint file. Forge and the validator are run from the repo root, so
write paths relative to it: `vocab/parties.csv`, `eval/tenancy.jsonl`.

## Sources: `structure.source_types`

Forge has loaders for `pdf`, `markdown`, `text` and `html`. The spec also lists
`docx` and `epub`; Forge rejects them. Convert those sources to markdown or PDF
first and declare the converted type.

## Chunking: `structure`

| Field | What Forge does |
|---|---|
| `hierarchy[].pattern` | Compiled with `re.MULTILINE`. Leading whitespace in a match is skipped before the position is recorded. |
| capture groups | Two groups: the second is the title. One group: it is the title. The **number is not in the chunk path**: `## Article 3 - Maximum deposit` becomes path `Deposits / Maximum deposit`. If users cite by number, keep the number in the title group. |
| `chunk_at` | One chunk per heading at this level. Text before the first such heading is not chunked. |
| `skip_regions` | Matched case-insensitively. `to_next_heading` stops at the next heading of any declared level. |
| `max_chunk_chars` | Oversized units are split at `subsplit_pattern`, else truncated at a word boundary. Truncation loses text: declare a `subsplit_pattern` for any corpus with long units. |
| `xref_patterns` | **Not parsed yet.** Relations listed in `deterministic_relations` are excluded from extraction but never written. Do not rely on them in a workload shape. |

Prove the structure section against the real loader before sign-off (SKILL.md
step 7). `inspect_corpus.py` uses its own regexes and cleanup; only `forge add`
and `forge chunks` show what Forge will actually produce.

## Extraction and the closed ontology

The ontology block (types, descriptions, properties, patterns) goes into the
extraction prompt as a cached prefix. After extraction, Forge **drops** every
entity whose label is not declared and every relation whose
`[source, RELATION, target]` is not in `patterns`. The drift report still counts
them as pattern violations, so a rising rate is evidence for `/bp-revise`.

Consequence for design: a pattern you forget to declare is an edge you will
never have. The pattern list is exhaustive by construction.

## Resolution: `ontology.vocabularies`

Forge reads the CSV named by `source`:

```csv
canonical,aliases
landlord,the landlord|landlords|lessor|owner
tenant,the tenant|tenants|lessee|occupier
```

Header `canonical,aliases`; aliases separated by `|`; matching is
case-insensitive. A missing file leaves the type unanchored and the validator
warns.

Tiers implemented: `exact` and `fuzzy` (`fuzzy_threshold`, needs `rapidfuzz`).
`embedding` and `llm_closed` are declared in the spec but not built; mentions
that miss the first two become new entities. Put the surface forms you already
know into `aliases` rather than relying on the later tiers.

## Embedding: `embedding`

| Field | What Forge does |
|---|---|
| `provider` | Only `local` (sentence-transformers). |
| `model`, `dimensions` | Passed to the embedder. A vector of the wrong width raises at embed time. |
| Chunk target `text` | May use only `{path}` and `{text}`. |
| Entity target `text` | May use only `{name}` and `{label}`. Resolved entities do not carry properties or neighbours through to embedding yet, so a card like `{name}. Used for: {purposes}` is not possible. Use `"{label}: {name}"`. |

Any other field in a template makes `format()` raise and takes down the
embedding step for the whole source. The validator rejects it.

## Drift

`drift` thresholds are checked on every `forge cook`. Exceeding one prints a
warning. `fail_on_exceeded: true` makes it a build failure instead; leave it
off for the first builds of a new blueprint, which are expected to trip
thresholds.

## Read by Serve and Agent, not Forge

`guardrails` (hard filters, conflict policy, answer policy) is compiled by Serve
and applied by Agent. `workload` and the golden set are design-time and eval
inputs; Forge does not read them.
