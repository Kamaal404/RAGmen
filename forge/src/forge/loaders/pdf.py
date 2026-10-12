# ruff: noqa: E501
"""PDF loader — extract clean text and per-page boundaries from PDF files.

Port of the graphrag-builder `extract_pdf.py` approach with the six bugs
listed in AGENTS.md fixed:

1. Measure chunk sizes from finditer positions, not re.split with capture groups.
2. ALL-CAPS regex `{3,}` → `{2,}` so 3-word headings like THE DAKOTA FIRE HOLE match.
3. Advance past leading whitespace when recording heading positions.
4. Record page offsets *after* cleanup (dehyphenation/reflow shortens text).
5. Measure width before any joining (already-joined lines inflate the stat).
6. Hierarchy levels re-sorted by rank after merging.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median


@dataclass(frozen=True, slots=True)
class PageInfo:
    """A single page boundary in the cleaned text."""

    page_num: int
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class LoaderResult:
    """Cleaned text with per-page character offsets."""

    text: str
    pages: list[PageInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Running-line detection
# ---------------------------------------------------------------------------

def _detect_running_lines(pages: list[str]) -> set[str]:
    """Return line fingerprints that appear on >50% of pages (headers/footers)."""
    if len(pages) < 2:
        return set()
    fp: Counter[str] = Counter()
    for p in pages:
        lines = [l for l in p.splitlines() if l.strip()]
        for l in set(
            re.sub(r"\d+", "#", ln).strip().lower()
            for ln in lines[:3] + lines[-3:]
        ):
            if l and len(l) > 3:
                fp[l] += 1
    threshold = max(2, int(len(pages) * 0.5))
    return {k for k, v in fp.items() if v >= threshold}


def _strip_running_lines(pages: list[str], running: set[str]) -> list[str]:
    """Remove detected running headers/footers from each page."""
    cleaned: list[str] = []
    for p in pages:
        lines = p.splitlines()
        nonblank = [i for i, l in enumerate(lines) if l.strip()]
        edge = set(nonblank[:3]) | set(nonblank[-3:])
        cleaned.append(
            "\n".join(
                l for i, l in enumerate(lines)
                if not (
                    i in edge
                    and re.sub(r"\d+", "#", l).strip().lower() in running
                )
            )
        )
    return cleaned


# ---------------------------------------------------------------------------
# Dehyphenation — bug 4: run BEFORE recording page boundaries
# ---------------------------------------------------------------------------

def _dehyphenate(text: str) -> str:
    """Remove trailing hyphens at line breaks where the next line continues."""
    return re.sub(r"(\w)-\n(\w)", r"\1\2\n\2", text)


# ---------------------------------------------------------------------------
# Reflow — bug 5: measure width BEFORE any joining
# ---------------------------------------------------------------------------

def _reflow_by_width(text: str) -> str:
    """Join continuation lines where lines are shorter than the median width.

    The median is computed from the *raw* text before any joining, so
    already-joined lines don't inflate it.
    """
    lines = text.splitlines()
    if not lines:
        return text

    widths = [len(l) for l in lines if l.strip()]
    if not widths:
        return text

    med = int(median(widths))
    # A continuation line is shorter than half the median — it's likely a
    # carried-over sentence fragment, not a new paragraph heading.
    threshold = med // 2

    out: list[str] = []
    for i, line in enumerate(lines):
        if i > 0 and line.strip() and len(line) < threshold and out[-1].strip():
            out[-1] = out[-1] + " " + line.lstrip()
        else:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def _pypdf_pages(path: Path, max_pages: int | None) -> list[str]:
    """Fallback: use pypdf to extract text."""
    try:
        from pypdf import PdfReader  # noqa: F811
    except ImportError:
        return []

    r = PdfReader(str(path))
    pages = [p.extract_text() or "" for p in r.pages]
    if max_pages is not None:
        pages = pages[:max_pages]
    return [p for p in pages if p.strip()]


def load_pdf(path: str | Path, max_pages: int | None = None) -> LoaderResult:
    """Extract clean text from a PDF with per-page char offsets.

    Uses pdftotext (poppler) first, then falls back to pypdf. Strips running
    headers/footers, dehyphenates, and reflows continuation lines.
    """
    path_obj = Path(path)

    # --- step 1: extract raw page text --------------------------------
    pages: list[str] = []
    try:
        res = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(path_obj), "-"],
            capture_output=True, text=True, timeout=300,
        )
        if res.returncode == 0:
            pages = [p for p in res.stdout.split("\f") if p.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not pages:
        # Bug-4 note: pypdf text is less clean, so running-line detection
        # may be weaker. Still better than returning nothing.
        pages = _pypdf_pages(path_obj, max_pages)
        if not pages:
            return LoaderResult(text="", pages=[])
        # pypdf doesn't use form-feeds so we split the old way
        pages_list: list[str] = []
        for p in pages:
            pages_list.append(p)
        pages = pages_list

    if max_pages is not None:
        pages = pages[:max_pages]

    # --- step 2: strip running headers/footers ------------------------
    running = _detect_running_lines(pages)
    pages = _strip_running_lines(pages, running)

    # --- step 3: dehyphenate + reflow on the joined text ---------------
    # Bug 4: dehyphenation changes text length, so we do it *before*
    # recording page boundaries.
    # Bug 5: width is measured on the raw text before any joining.
    for i, p in enumerate(pages):
        p = _dehyphenate(p)
        p = _reflow_by_width(p)
        pages[i] = p

    # --- step 4: join and record page boundaries AFTER cleanup (bug 4) --
    fragments: list[str] = []
    page_map: list[PageInfo] = []
    offset = 0

    for i, p in enumerate(pages):
        # Separate pages with a single newline if the page didn't end with one
        sep = "\n" if fragments and not fragments[-1].endswith("\n") else ""
        fragments.append(sep + p)
        end = offset + len(sep + p)
        page_map.append(PageInfo(page_num=i + 1, char_start=offset, char_end=end))
        offset = end

    text = "".join(fragments)
    return LoaderResult(text=text, pages=page_map)


__all__ = ["LoaderResult", "PageInfo", "load_pdf"]