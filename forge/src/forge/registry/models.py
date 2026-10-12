# ruff: noqa: E501

"""Immutable records persisted by the Forge registry."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Source:
    """A registered source version and its blueprint provenance."""

    uri: str
    content_hash: str
    mime: str
    blueprint_version: str
    blueprint_hash: str
    source_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    added_by: str = ""
    added_at: datetime = field(default_factory=_utc_now)
    visibility: str = "public"
    license: str = "unknown"
    status: str = "pending"
    chunk_count: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class Chunk:
    """A content-addressed, provenance-bearing source span."""

    source_id: str
    path: str
    text: str
    char_start: int
    char_end: int
    blueprint_hash: str
    chunk_id: str = ""
    page: int | None = None
    xrefs: str = "[]"
    ingest_job_id: str | None = None

    def __post_init__(self) -> None:
        if not self.chunk_id:
            object.__setattr__(self, "chunk_id", derive_chunk_id(self.source_id, self.path, self.text))


@dataclass(frozen=True, slots=True)
class IngestJob:
    """A resumable ingest operation for one source and blueprint."""

    source_id: str
    blueprint_hash: str
    total_chunks: int = 0
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=_utc_now)
    completed_at: datetime | None = None
    status: str = "running"
    completed_chunks: int = 0
    error: str | None = None


def derive_chunk_id(source_id: str, path: str, text: str) -> str:
    """Return SHA-256 of the three fields separated by NUL bytes."""
    payload = "\0".join((source_id, path, text)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
