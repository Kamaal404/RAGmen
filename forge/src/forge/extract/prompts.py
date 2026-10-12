"""Prompt assembly for extraction — cache-prefix-first ontology layout.

The ontology block is byte-identical across every chunk in a run, so it is
laid out as the *first* content in the system message. With prompt caching
(OpenAI, Anthropic), this prefix is cached after the first chunk and charged
once instead of N times.

Cost fact from ARCHITECTURE.md: the ontology block is 40 to 97% of input
tokens on real corpora. Getting it cached is the single largest available
saving.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Cache-prefix assembly
# ---------------------------------------------------------------------------

SYSTEM_HEAD = """You are a precise information extractor for a knowledge graph.
Your task is to extract entities and relationships from the text below,
following the ontology exactly.

Never invent labels, types, or relationships that are not declared below.
If a span seems meaningful but does not fit any declared type, record it as
an unmapped mention so the ontology maintainer can evaluate it.

Extract verbatim quotes for every claim. Do not paraphrase or normalise
surface forms.

Return JSON with this exact structure:
{
  "entities": [
    {
      "label": "<declared node type label>",
      "name": "<canonical name from the text>",
      "properties": {"<prop>": <value>, ...},
      "quote": "<verbatim span justifying this entity>",
      "char_start": <int>,
      "char_end": <int>
    }
  ],
  "relations": [
    {
      "source_label": "<declared node type>",
      "source_name": "<name verbatim from entity above>",
      "relation": "<declared relationship label>",
      "target_label": "<declared node type>",
      "target_name": "<name verbatim from entity above>",
      "properties": {"<prop>": <value>, ...},
      "quote": "<verbatim span>",
      "char_start": <int>,
      "char_end": <int>
    }
  ],
  "unmapped_mentions": ["<verbatim span that seems meaningful but has no type>"]
}
"""


def _fmt_entity_type(nt: dict) -> str:
    props = nt.get("properties", [])
    prop_lines = "\n".join(
        f"    - {p['name']} ({p.get('type', 'STRING')})"
        + (f" — {p.get('description', '')}" if p.get("description") else "")
        + (" [required]" if p.get("required") else "")
        + (f" enum: {p['enum']}" if p.get("enum") else "")
        for p in props
    )
    return (
        f"  {nt['label']}: {nt.get('description', '')}\n"
        + (f"    Answers: {nt.get('answers', '')}\n" if nt.get("answers") else "")
        + (prop_lines if prop_lines else "    (no properties)")
    )


def _fmt_relation_type(rt: dict) -> str:
    props = rt.get("properties", [])
    prop_lines = "\n".join(
        f"    - {p['name']} ({p.get('type', 'STRING')})"
        + (f" — {p.get('description', '')}" if p.get("description") else "")
        + (f" enum: {p['enum']}" if p.get("enum") else "")
        for p in props
    )
    return (
        f"  {rt['label']}: {rt.get('description', '')}\n"
        + (prop_lines if prop_lines else "")
    )


def _fmt_patterns(patterns: list[list[str]]) -> str:
    if not patterns:
        return "  (none)"
    return "\n".join(f"  {p[0]} --[{p[1]}]--> {p[2]}" for p in patterns)


def build_ontology_block(ontology: dict) -> str:
    """Build the cache-prefix ontology block.

    This is the FIRST content in the system message so prompt caching can
    key on its prefix across every chunk in a run.
    """
    lines: list[str] = [
        "## ONTOLOGY (closed — extract ONLY these types)",
        "",
        "### Node types:",
    ]

    for nt in ontology.get("node_types", []):
        lines.append("")
        lines.append(_fmt_entity_type(nt))

    lines.extend(["", "### Relationship types:"])
    for rt in ontology.get("relationship_types", []):
        lines.append("")
        lines.append(_fmt_relation_type(rt))

    lines.extend(["", "### Allowed patterns (source --[relation]--> target):"])
    lines.append(_fmt_patterns(ontology.get("patterns", [])))

    det = ontology.get("deterministic_relations", [])
    if det:
        lines.extend(["", "### Deterministic relations (do NOT extract these):"])
        lines.append(f"  {', '.join(det)} — these are parsed from xref_patterns, not extracted.")

    lines.extend([
        "",
        "### Extraction rules:",
        "  1. Only use labels listed above. Never invent new ones.",
        "  2. Extract verbatim quotes. Do not paraphrase.",
        "  3. char_start/char_end are byte offsets in the source chunk.",
        "  4. Set confidence to 0.0-1.0. Lower it when the match is ambiguous.",
        "  5. If a span is meaningful but fits no type, add it to unmapped_mentions.",
        "  6. For vocabulary-controlled types, prefer the canonical form. If it differs",
        "     from the text, note the surface form in properties.surface_form.",
        "  7. An entity that appears multiple times in the same chunk should be",
        "     extracted once with all relevant relations.",
    ])

    return "\n".join(lines)


def build_chunk_prompt(
    chunk_text: str,
    chunk_path: str,
    ontology_block: str,
    provenance_require_page: bool = False,
) -> list[dict[str, str]]:
    """Build a full message list for one chunk.

    The ontology_block is the *shared cache prefix* — byte-identical across
    every chunk call. Only the user message varies per chunk.

    Returns OpenAI-format messages: [{"role": "system", "content": ...}, {"role": "user", "content": ...}]
    """
    user_lines = [f"### Source chunk: {chunk_path}", "", chunk_text]
    if provenance_require_page:
        user_lines.insert(0, "Note: include page numbers where available.")

    return [
        {"role": "system", "content": SYSTEM_HEAD + "\n\n" + ontology_block},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token). Accurate enough for cost reports."""
    return len(text) // 4


def estimate_prompt_cost(
    ontology_block: str,
    chunk_text: str,
    chunk_path: str,
    model: str = "gpt-4o",
    cached: bool = False,
) -> dict[str, float | int]:
    """Estimate token usage and cost for one extraction call.

    Returns dict with keys: total_tokens, cached_tokens, new_tokens, cost_usd.

    When *cached* is True (all chunks after the first), the ontology block
    tokens are charged at the cached rate.
    """
    # Rough pricing (per 1M tokens) for common models
    _PRICING: dict[str, tuple[float, float, float, float]] = {
        "gpt-4o": (2.50, 10.00, 1.25, 5.00),  # input, output, cached_input, cached_output
        "gpt-4o-mini": (0.15, 0.60, 0.075, 0.30),
        "claude-sonnet-4-20250514": (3.00, 15.00, 0.30, 1.50),
        "claude-haiku-3-5-20241022": (0.80, 4.00, 0.08, 0.40),
    }

    sys_prompt = SYSTEM_HEAD + "\n\n" + ontology_block
    user_prompt = f"### Source chunk: {chunk_path}\n\n{chunk_text}"

    sys_tokens = estimate_tokens(sys_prompt)
    user_tokens = estimate_tokens(user_prompt)

    if cached:
        input_cost_rate, output_rate, cached_rate, cached_output_rate = _PRICING.get(
            model, (3.0, 15.0, 0.30, 1.50)
        )
        # System prompt is cached; user prompt is new
        input_cost = (sys_tokens / 1_000_000) * cached_rate
        input_cost += (user_tokens / 1_000_000) * input_cost_rate
    else:
        input_cost_rate, output_rate, _, _ = _PRICING.get(model, (3.0, 15.0, 0.30, 1.50))
        input_cost = (sys_tokens + user_tokens) / 1_000_000 * input_cost_rate

    # Estimated output (conservative: ~200 tokens per extraction)
    estimated_output = max(200, user_tokens // 10)
    output_cost = (estimated_output / 1_000_000) * output_rate

    return {
        "total_tokens": sys_tokens + user_tokens + estimated_output,
        "cached_tokens": sys_tokens if cached else 0,
        "new_tokens": user_tokens + estimated_output,
        "cost_usd": round(input_cost + output_cost, 6),
    }


__all__ = [
    "SYSTEM_HEAD",
    "build_ontology_block",
    "build_chunk_prompt",
    "estimate_tokens",
    "estimate_prompt_cost",
]