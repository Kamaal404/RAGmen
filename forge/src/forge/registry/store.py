# ruff: noqa: E501

"""SQLite persistence for Forge's source registry."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from .models import Chunk, IngestJob, Source

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY, uri TEXT NOT NULL, content_hash TEXT NOT NULL,
    mime TEXT NOT NULL, added_by TEXT NOT NULL, added_at TEXT NOT NULL,
    visibility TEXT NOT NULL, license TEXT NOT NULL, blueprint_version TEXT NOT NULL,
    blueprint_hash TEXT NOT NULL, status TEXT NOT NULL, chunk_count INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_uri ON sources(uri);
CREATE TABLE IF NOT EXISTS ingest_jobs (
    job_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
    blueprint_hash TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
    status TEXT NOT NULL, total_chunks INTEGER NOT NULL, completed_chunks INTEGER NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON ingest_jobs(source_id);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    path TEXT NOT NULL, text TEXT NOT NULL, char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
    page INTEGER, xrefs TEXT NOT NULL, blueprint_hash TEXT NOT NULL,
    ingest_job_id TEXT REFERENCES ingest_jobs(job_id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
CREATE TABLE IF NOT EXISTS job_chunk_status (
    job_id TEXT NOT NULL REFERENCES ingest_jobs(job_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    status TEXT NOT NULL, error TEXT, PRIMARY KEY(job_id, chunk_id)
);
CREATE TABLE IF NOT EXISTS entity_refcounts (
    entity_id TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    label TEXT NOT NULL, name TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY(entity_id, source_id)
);
"""


def _stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class RegistryStore:
    """Transactional SQLite registry adapter."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def __enter__(self) -> RegistryStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.connection.rollback()
        self.close()

    def close(self) -> None:
        self.connection.close()

    def add_source(self, source: Source) -> Source:
        self.connection.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source.source_id, source.uri, source.content_hash, source.mime, source.added_by,
             _stamp(source.added_at), source.visibility, source.license, source.blueprint_version,
             source.blueprint_hash, source.status, source.chunk_count, source.cost_usd),
        )
        self.connection.commit()
        return source

    def get_source(self, source_id: str) -> Source | None:
        row = self.connection.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        return self._source(row) if row else None

    def get_source_by_uri(self, uri: str) -> Source | None:
        row = self.connection.execute("SELECT * FROM sources WHERE uri = ? ORDER BY added_at DESC LIMIT 1", (uri,)).fetchone()
        return self._source(row) if row else None

    def list_sources(self) -> list[Source]:
        return [self._source(row) for row in self.connection.execute("SELECT * FROM sources ORDER BY added_at")]

    def update_source_status(self, source_id: str, status: str) -> None:
        self.connection.execute("UPDATE sources SET status = ? WHERE source_id = ?", (status, source_id))
        self.connection.commit()

    def add_chunks(self, chunks: Iterable[Chunk]) -> int:
        values = [(c.chunk_id, c.source_id, c.path, c.text, c.char_start, c.char_end, c.page, c.xrefs, c.blueprint_hash, c.ingest_job_id) for c in chunks]
        cursor = self.connection.executemany("INSERT OR IGNORE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        self.connection.commit()
        if values:
            source_id = values[0][1]
            self.connection.execute("UPDATE sources SET chunk_count = (SELECT COUNT(*) FROM chunks WHERE source_id = ?) WHERE source_id = ?", (source_id, source_id))
            self.connection.commit()
        return cursor.rowcount

    def get_chunks_for_source(self, source_id: str) -> list[Chunk]:
        return [self._chunk(row) for row in self.connection.execute("SELECT * FROM chunks WHERE source_id = ? ORDER BY char_start", (source_id,))]

    def chunk_exists(self, chunk_id: str) -> bool:
        return self.connection.execute("SELECT 1 FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone() is not None

    def create_job(self, job: IngestJob) -> IngestJob:
        self.connection.execute("INSERT INTO ingest_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (job.job_id, job.source_id, job.blueprint_hash, _stamp(job.started_at), _stamp(job.completed_at), job.status, job.total_chunks, job.completed_chunks, job.error))
        self.connection.commit()
        return job

    def get_job(self, job_id: str) -> IngestJob | None:
        row = self.connection.execute("SELECT * FROM ingest_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def update_job_status(
        self,
        job_id: str,
        status: str,
        completed_chunks: int | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        fields = ["status = ?", "error = ?", "completed_at = ?"]
        values: list[str | int | None] = [status, error, _stamp(completed_at)]
        if completed_chunks is not None:
            fields.append("completed_chunks = ?")
            values.append(completed_chunks)
        values.append(job_id)
        self.connection.execute(
            f"UPDATE ingest_jobs SET {', '.join(fields)} WHERE job_id = ?",
            values,
        )
        self.connection.commit()

    def set_chunk_status(self, job_id: str, chunk_id: str, status: str, error: str | None = None) -> None:
        self.connection.execute("INSERT INTO job_chunk_status VALUES (?, ?, ?, ?) ON CONFLICT(job_id, chunk_id) DO UPDATE SET status=excluded.status, error=excluded.error", (job_id, chunk_id, status, error))
        self.connection.execute("UPDATE ingest_jobs SET completed_chunks = (SELECT COUNT(*) FROM job_chunk_status WHERE job_id = ? AND status IN ('extracted', 'embedded', 'written')) WHERE job_id = ?", (job_id, job_id))
        self.connection.commit()

    def mark_chunks_pending(self, job_id: str, chunk_ids: Iterable[str]) -> None:
        self.connection.executemany("INSERT OR IGNORE INTO job_chunk_status(job_id, chunk_id, status) VALUES (?, ?, 'pending')", ((job_id, chunk_id) for chunk_id in chunk_ids))
        self.connection.commit()

    def get_resume_point(self, job_id: str) -> set[str]:
        rows = self.connection.execute("SELECT chunk_id FROM job_chunk_status WHERE job_id = ? AND status != 'pending'", (job_id,))
        return {row[0] for row in rows}

    def get_incomplete_chunks(self, job_id: str) -> list[str]:
        rows = self.connection.execute("SELECT chunk_id FROM job_chunk_status WHERE job_id = ? AND status IN ('pending', 'failed')", (job_id,))
        return [row[0] for row in rows]

    def increment_entity_refcount(self, entity_id: str, source_id: str, label: str, name: str) -> None:
        self.connection.execute("INSERT INTO entity_refcounts VALUES (?, ?, ?, ?, 1) ON CONFLICT(entity_id, source_id) DO UPDATE SET count=count+1", (entity_id, source_id, label, name))
        self.connection.commit()

    def decrement_entity_refcount(self, entity_id: str, source_id: str) -> int:
        self.connection.execute("UPDATE entity_refcounts SET count=count-1 WHERE entity_id=? AND source_id=? AND count > 0", (entity_id, source_id))
        row = self.connection.execute("SELECT count FROM entity_refcounts WHERE entity_id=? AND source_id=?", (entity_id, source_id)).fetchone()
        count = int(row[0]) if row else 0
        if count == 0:
            self.connection.execute("DELETE FROM entity_refcounts WHERE entity_id=? AND source_id=?", (entity_id, source_id))
        self.connection.commit()
        return count

    def retract_source(self, source_id: str) -> bool:
        cursor = self.connection.execute("UPDATE sources SET status = 'retracted', chunk_count = 0 WHERE source_id = ? AND status != 'retracted'", (source_id,))
        self.connection.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        self.connection.execute("DELETE FROM entity_refcounts WHERE source_id = ?", (source_id,))
        self.connection.commit()
        return cursor.rowcount == 1

    @staticmethod
    def _source(row: sqlite3.Row) -> Source:
        return Source(row["uri"], row["content_hash"], row["mime"], row["blueprint_version"], row["blueprint_hash"], row["source_id"], row["added_by"], datetime.fromisoformat(row["added_at"]), row["visibility"], row["license"], row["status"], row["chunk_count"], row["cost_usd"])

    @staticmethod
    def _chunk(row: sqlite3.Row) -> Chunk:
        return Chunk(row["source_id"], row["path"], row["text"], row["char_start"], row["char_end"], row["blueprint_hash"], row["chunk_id"], row["page"], row["xrefs"], row["ingest_job_id"])

    @staticmethod
    def _job(row: sqlite3.Row) -> IngestJob:
        return IngestJob(row["source_id"], row["blueprint_hash"], row["total_chunks"], row["job_id"], datetime.fromisoformat(row["started_at"]), _time(row["completed_at"]), row["status"], row["completed_chunks"], row["error"])
