"""Source loaders dispatcher.

Maps a MIME type to the loader function that produces LoaderResult.
"""

from __future__ import annotations

from pathlib import Path

from .pdf import load_pdf
from .text import load_html, load_markdown, load_text

MIME_MAP: dict[str, tuple[str, str]] = {
    # (mime_type, file_extension) -> (loader_function_name, description)
}


def loader_for(path: str | Path) -> str:
    """Return the MIME type for a source file based on its extension."""
    ext = Path(path).suffix.lower()
    _EXT_TO_MIME = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
    }
    mime = _EXT_TO_MIME.get(ext)
    if mime is None:
        raise ValueError(f"unsupported source type: {ext} (path: {path})")
    return mime


def load_source(path: str | Path) -> tuple[str, str]:
    """Load a source file and return (mime_type, raw_text).

    Returns the raw text before any structure-aware chunking. The chunking
    step (forge.chunking) is responsible for applying the blueprint.
    """
    path_obj = Path(path)
    ext = path_obj.suffix.lower()
    mime = loader_for(path_obj)

    if mime == "application/pdf":
        result = load_pdf(path_obj)
    elif mime == "text/markdown":
        result = load_markdown(path_obj)
    elif mime == "text/html":
        result = load_html(path_obj)
    else:
        result = load_text(path_obj)

    return mime, result.text


__all__ = ["loader_for", "load_source"]