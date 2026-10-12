# ruff: noqa: E501
"""Loaders for non-paginated source types: text, markdown, HTML.

Each function returns a LoaderResult with the full text and a single-page
page map. For PDF sources see pdf.py.
"""

from __future__ import annotations

import re
import html.parser
from dataclasses import dataclass, field
from pathlib import Path


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
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Normalise line endings and strip BOM."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.lstrip("\ufeff")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_text(path: str | Path) -> LoaderResult:
    """Load a plain-text file as a single-page document."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    text = _normalise(text)
    return LoaderResult(
        text=text,
        pages=[PageInfo(page_num=1, char_start=0, char_end=len(text))],
    )


def load_markdown(path: str | Path) -> LoaderResult:
    """Load a markdown file as a single-page document.

    Currently identical to load_text. In a later phase the markdown loader may
    preserve richer structure (code fences, list indentation) for the chunker.
    """
    return load_text(path)


class _HTMLStripper(html.parser.HTMLParser):
    """Strip HTML tags, preserving block-level structure as newlines."""

    BLOCK_TAGS = {
        "p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "ol", "ul", "blockquote", "pre", "hr", "tr", "td", "th",
        "section", "article", "nav", "header", "footer", "main",
    }
    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self._parts)
        # Collapse multiple consecutive newlines into at most two
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def load_html(path: str | Path) -> LoaderResult:
    """Load an HTML file, strip tags, and return the readable text."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    raw = _normalise(raw)
    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.text
    return LoaderResult(
        text=text,
        pages=[PageInfo(page_num=1, char_start=0, char_end=len(text))],
    )


__all__ = ["LoaderResult", "PageInfo", "load_text", "load_markdown", "load_html"]