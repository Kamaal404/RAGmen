# ruff: noqa: E501

from datetime import UTC, datetime

from forge.registry import Chunk, IngestJob, RegistryStore, Source, derive_chunk_id


def source(uri: str = "manual.md") -> Source:
    return Source(uri, "sha256:source", "text/markdown", "1.0.0", "sha256:blueprint", added_at=datetime.now(UTC))


def test_chunk_id_is_deterministic_and_field_sensitive() -> None:
    expected = derive_chunk_id("source", "path", "text")
    assert expected == derive_chunk_id("source", "path", "text")
    assert expected != derive_chunk_id("other", "path", "text")
    assert expected != derive_chunk_id("source", "other", "text")
    assert expected != derive_chunk_id("source", "path", "other")


def test_source_and_chunks_are_persisted_and_deduplicated(tmp_path) -> None:
    with RegistryStore(tmp_path / "registry.db") as store:
        registered = store.add_source(source())
        chunk = Chunk(registered.source_id, "Chapter 1", "content", 0, 7, registered.blueprint_hash)
        assert store.add_chunks([chunk, chunk]) == 1
        assert store.get_chunks_for_source(registered.source_id) == [chunk]
        stored = store.get_source(registered.source_id)
        assert stored is not None
        assert stored.chunk_count == 1


def test_job_resume_tracks_completed_chunks(tmp_path) -> None:
    with RegistryStore(tmp_path / "registry.db") as store:
        registered = store.add_source(source())
        chunks = [Chunk(registered.source_id, str(i), str(i), i, i + 1, registered.blueprint_hash) for i in range(3)]
        store.add_chunks(chunks)
        job = store.create_job(IngestJob(registered.source_id, registered.blueprint_hash, total_chunks=3))
        store.mark_chunks_pending(job.job_id, (chunk.chunk_id for chunk in chunks))
        store.set_chunk_status(job.job_id, chunks[0].chunk_id, "written")
        assert store.get_resume_point(job.job_id) == {chunks[0].chunk_id}
        assert set(store.get_incomplete_chunks(job.job_id)) == {
            chunks[1].chunk_id,
            chunks[2].chunk_id,
        }


def test_retraction_removes_chunks_and_preserves_other_source_refcount(tmp_path) -> None:
    with RegistryStore(tmp_path / "registry.db") as store:
        first = store.add_source(source("first.md"))
        second = store.add_source(source("second.md"))
        store.add_chunks([Chunk(first.source_id, "p", "one", 0, 3, first.blueprint_hash)])
        store.add_chunks([Chunk(second.source_id, "p", "two", 0, 3, second.blueprint_hash)])
        store.increment_entity_refcount("entity", first.source_id, "Thing", "shared")
        store.increment_entity_refcount("entity", second.source_id, "Thing", "shared")
        assert store.retract_source(first.source_id)
        assert store.get_chunks_for_source(first.source_id) == []
        stored = store.get_source(second.source_id)
        assert stored is not None
        assert stored.status == "pending"
        assert store.decrement_entity_refcount("entity", first.source_id) == 0
        assert store.decrement_entity_refcount("entity", second.source_id) == 0
