# ruff: noqa: E501
"""Blueprint-driven chunking — turn raw text into content-addressed Chunks.

Applies the blueprint's structure section: hierarchy patterns to find headings,
skip_regions to exclude non-content, chunk_at to determine boundaries,
max_chunk_chars + subsplit_pattern for oversized units.

Bugs already fixed, do not reintroduce (see AGENTS.md):
1. re.split with capture groups → measure from finditer positions.
2. ALL-CAPS {3,} → {2,} so 3-word headings match.
3. Leading whitespace before heading — advance past it before recording position.
4. Page offsets after cleanup — already handled by loaders.
5. Reflow by line width, not punctuation — handled by loaders.
6. Hierarchy re-sort by rank — handled here.
"""

from __future__ import annotations

import re
from typing import Any

from forge.registry.models import Chunk  # noqa: TC001


# ---------------------------------------------------------------------------
# Skip region processing
# ---------------------------------------------------------------------------

def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or touching character intervals."""
    if not intervals:
        return []
    sorted_iv = sorted(intervals)
    merged: list[tuple[int, int]] = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _is_skipped(pos: int, intervals: list[tuple[int, int]]) -> bool:
    """Return True if *pos* falls inside any skip interval."""
    for start, end in intervals:
        if start <= pos < end:
            return True
    return False


def apply_skip_regions(
    text: str,
    skip_regions: list[dict],
    heading_patterns: list[re.Pattern] | None = None,
) -> list[tuple[int, int]]:
    """Return character intervals to skip, given the blueprint's skip_regions.

    Each region dict has:
      - match: regex pattern (MULTILINE)
      - span: 'line' | 'to_next_heading' | 'to_end' (default 'to_next_heading')
      - reason: optional note

    When *heading_patterns* is provided (compiled hierarchy patterns), the
    ``to_next_heading`` span will stop at the *first* heading at any level.
    Without it, ``to_next_heading`` falls back to the same match pattern.
    """
    intervals: list[tuple[int, int]] = []

    for region in skip_regions:
        try:
            rx = re.compile(region["match"], re.MULTILINE | re.IGNORECASE)
        except re.error:
            continue

        span = region.get("span", "to_next_heading")
        for m in rx.finditer(text):
            # Bug 3: advance past leading whitespace
            raw_start = m.start()
            start = raw_start
            while start < m.end() and text[start] in (" ", "\t", "\n", "\r"):
                start += 1

            if span == "line":
                # Skip to end of line containing the match
                line_end = text.find("\n", start)
                if line_end == -1:
                    line_end = len(text)
                else:
                    line_end += 1  # include the newline
                intervals.append((start, line_end))
            elif span == "to_end":
                intervals.append((start, len(text)))
            elif span == "to_next_heading":
                # Stop at the next heading at any hierarchy level, or end
                next_heading: int | None = None
                if heading_patterns:
                    for hp in heading_patterns:
                        hm = hp.search(text, start + 1)
                        if hm:
                            cand = hm.start()
                            # Bug 3: advance past leading whitespace on the candidate
                            adj = cand
                            while adj < hm.end() and text[adj] in (" ", "\t", "\n", "\r"):
                                adj += 1
                            if next_heading is None or adj < next_heading:
                                next_heading = adj
                # Also check the same region's own pattern
                for om in rx.finditer(text, start + 1):
                    cand = om.start()
                    if next_heading is None or cand < next_heading:
                        next_heading = cand
                end = next_heading if next_heading is not None else len(text)
                intervals.append((start, end))

    return _merge_intervals(intervals)


# ---------------------------------------------------------------------------
# Hierarchy parsing
# ---------------------------------------------------------------------------

def _compile_hierarchy(hierarchy: list[dict]) -> list[tuple[str, re.Pattern, bool]]:
    """Compile each hierarchy level's pattern.

    Returns list of (level_name, compiled_pattern, has_title_capture) in order.
    """
    result: list[tuple[str, re.Pattern, bool]] = []
    for entry in hierarchy:
        level = entry["level"]
        raw = entry["pattern"]
        try:
            rx = re.compile(raw, re.MULTILINE)
        except re.error:
            continue
        # Does the pattern have a second capture group for the title?
        groups = rx.groups
        result.append((level, rx, groups >= 2))
    return result


def _heading_position(match: re.Match) -> tuple[int, int]:
    """Return (start, end) for a heading match.

    Bug 3 fix: advance past leading whitespace when recording position.
    """
    start = match.start()
    # Advance past leading whitespace so chunk boundaries don't land one
    # character early on the newline before the heading.
    text = match.string
    while start < match.end() and text[start] in (" ", "\t", "\n", "\r"):
        start += 1
    return (start, match.end())


def _extract_title(match: re.Match, has_title: bool) -> str:
    """Extract the heading title from a match.

    If the pattern has 2 capture groups (number, title), use group(2).
    If the pattern has 1 capture group (the title alone), use group(1).
    Otherwise use the full match stripped.
    """
    if has_title and match.lastindex and match.lastindex >= 2:
        return match.group(2).strip()
    if match.lastindex and match.lastindex >= 1:
        return match.group(1).strip()
    title = match.group(0).strip()
    return title.strip(".:;–—-\t ")


def _resolve_breadcrumb(
    char: int,
    heading_positions: dict[str, list[tuple[int, int, str, int]]],
) -> str:
    """Build the hierarchy breadcrumb path for a given character offset.

    For each hierarchy level (outermost first), find the most recent heading
    before *char* and append its title. Returns "/" separated path.
    """
    crumbs: list[str] = []
    for level, positions in heading_positions.items():
        current: str | None = None
        for pos_start, pos_end, title, _ in positions:
            # Check if the heading itself starts before our target
            # Bug 3: use the adjusted (advanced) position
            if pos_start <= char:
                current = title
            else:
                break
        if current:
            crumbs.append(current)
    return " / ".join(crumbs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _subtract_skips(
    span_start: int, span_end: int, skip_intervals: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Remove skip intervals from a character span, returning clean sub-spans."""
    result: list[tuple[int, int]] = []
    cursor = span_start
    for s_start, s_end in sorted(skip_intervals):
        if s_end <= cursor:
            continue
        if s_start >= span_end:
            break
        if s_start > cursor:
            result.append((cursor, min(s_start, span_end)))
        cursor = max(cursor, s_end)
    if cursor < span_end:
        result.append((cursor, span_end))
    return result


def chunk_document(
    text: str,
    structure: dict,
    source_id: str,
    blueprint_hash: str,
) -> list[Chunk]:
    """Split a document into Chunks according to the blueprint structure.

    Args:
        text: Cleaned source text (from loaders).
        structure: The blueprint's "structure" section dict.
        source_id: Registry source ID.
        blueprint_hash: Blueprint content hash to stamp on each chunk.

    Returns:
        List of Chunk objects (content-addressed via derive_chunk_id).
    """
    # --- 1. Skip regions ------------------------------------------------
    skip_regions = structure.get("skip_regions", [])

    # --- 2. Compile hierarchy (needed early for skip-region heading detection)
    hierarchy_raw = structure.get("hierarchy", [])
    compiled = _compile_hierarchy(hierarchy_raw) if hierarchy_raw else []
    heading_patterns = [rx for _, rx, _ in compiled]
    skip_intervals = apply_skip_regions(text, skip_regions, heading_patterns)

    if not compiled:
        chunk = Chunk(source_id, "", text.strip(), 0, len(text), blueprint_hash)
        return [chunk]

    chunk_at = structure.get("chunk_at")
    if not chunk_at or chunk_at not in {level for level, _, _ in compiled}:
        chunk = Chunk(source_id, "", text.strip(), 0, len(text), blueprint_hash)
        return [chunk]

    # heading_positions[level_name] = list of (adjusted_start, end, title, original_start)
    heading_positions: dict[str, list[tuple[int, int, str, int]]] = {}
    for level, rx, has_title in compiled:
        positions: list[tuple[int, int, str, int]] = []
        for m in rx.finditer(text):
            start_adj, end = _heading_position(m)
            title = _extract_title(m, has_title)
            positions.append((start_adj, end, title, m.start()))
        positions.sort(key=lambda x: x[0])
        heading_positions[level] = positions

    # --- 4. Determine chunk boundaries at the chunk_at level -------------
    chunk_at_positions = heading_positions.get(chunk_at, [])
    if not chunk_at_positions:
        # No headings at the chunk_at level → one chunk, but still subtract skips
        sub_spans = _subtract_skips(0, len(text), skip_intervals)
        parts = [text[s:e].strip() for s, e in sub_spans if text[s:e].strip()]
        clean = "\n\n".join(parts)
        if not clean:
            clean = text.strip()
        chunk = Chunk(source_id, "", clean, 0, len(text), blueprint_hash)
        return [chunk]

    # Build boundaries: [start_of_chunk_1, start_of_chunk_2, ..., end_of_text]
    boundaries: list[int] = [p[0] for p in chunk_at_positions]
    boundaries.append(len(text))

    # --- 5. Create Chunks ------------------------------------------------
    max_chunk_chars = structure.get("max_chunk_chars", 4000)
    subsplit_pattern = structure.get("subsplit_pattern")

    chunks: list[Chunk] = []
    for i in range(len(boundaries) - 1):
        chunk_start = boundaries[i]
        chunk_end = boundaries[i + 1]

        # Skip empty spans
        if chunk_start >= chunk_end:
            continue

        sub_spans = _subtract_skips(chunk_start, chunk_end, skip_intervals)
        if not sub_spans:
            continue  # fully skipped

        # Build hierarchy path (from the original heading at chunk_start)
        path = _resolve_breadcrumb(chunk_start, heading_positions)

        for span_start, span_end in sub_spans:
            chunk_text = text[span_start:span_end].strip()
            if not chunk_text:
                continue

            # Handle subsplit for oversized chunks
            if subsplit_pattern and len(chunk_text) > max_chunk_chars:
                try:
                    sub_rx = re.compile(subsplit_pattern, re.MULTILINE)
                except re.error:
                    sub_rx = None
                if sub_rx:
                    sub_matches = list(sub_rx.finditer(chunk_text))
                    if sub_matches:
                        sub_boundaries = [m.start() for m in sub_matches]
                        sub_boundaries.append(len(chunk_text))
                        for j in range(len(sub_boundaries) - 1):
                            s_start = sub_boundaries[j]
                            s_end = sub_boundaries[j + 1]
                            sub_text = chunk_text[s_start:s_end].strip()
                            if not sub_text:
                                continue
                            sub_path = path + " / " + sub_text.split("\n")[0].strip()[:60]
                            abs_start = span_start + s_start
                            abs_end = span_start + s_end
                            chk = Chunk(
                                source_id=source_id,
                                path=sub_path,
                                text=sub_text,
                                char_start=abs_start,
                                char_end=abs_end,
                                blueprint_hash=blueprint_hash,
                            )
                            chunks.append(chk)
                        continue

            # Truncate at max_chunk_chars if still oversized
            if len(chunk_text) > max_chunk_chars:
                truncated = chunk_text[:max_chunk_chars]
                last_space = truncated.rfind(" ")
                if last_space > max_chunk_chars // 2:
                    chunk_text = truncated[:last_space]
                    span_end = span_start + len(chunk_text)
                else:
                    chunk_text = truncated
                    span_end = span_start + max_chunk_chars

            chk = Chunk(
                source_id=source_id,
                path=path,
                text=chunk_text,
                char_start=span_start,
                char_end=span_end,
                blueprint_hash=blueprint_hash,
            )
            chunks.append(chk)

    return chunks


__all__ = ["Chunk", "apply_skip_regions", "chunk_document"]