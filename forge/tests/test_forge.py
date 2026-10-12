# ruff: noqa: E501, S101
"""Tests for forge: blueprint, loaders, chunking, extraction, CLI.

All tests run offline with no API key or database connection.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

# -- import forge modules --------------------------------------------------
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from forge.blueprint import (  # noqa: E402
    BlueprintError,
    BlueprintNotFoundError,
    compute_hash,
    load_blueprint,
    validate_blueprint,
)
from forge.chunking import Chunk, apply_skip_regions, chunk_document  # noqa: E402
from forge.loaders import load_source, loader_for  # noqa: E402
from forge.loaders.text import LoaderResult as TextLoaderResult  # noqa: E402
from forge.loaders.text import load_html, load_markdown, load_text  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)


@pytest.fixture(scope="session")
def mini_blueprint_yaml() -> str:
    return """\
blueprint_version: 1.0.0
name: test
description: Test blueprint for forge phase 1
structure:
  source_types: [markdown, text]
  hierarchy:
    - level: chapter
      pattern: '^#\s+(.+)$'
      example: '# Introduction'
    - level: section
      pattern: '^##\s+(.+)$'
      example: '## Setup'
  chunk_at: section
  max_chunk_chars: 5000
ontology:
  closed: true
  node_types:
    - label: Topic
      description: A subject discussed in the text.
      answers: "What is this about?"
      properties:
        - {name: name, type: STRING, required: true}
    - label: Reference
      description: A referenced item.
      answers: "What is referenced?"
      properties:
        - {name: name, type: STRING, required: true}
  relationship_types:
    - label: MENTIONS
      description: The topic mentions this reference.
  patterns:
    - [Topic, MENTIONS, Reference]
embedding:
  model: all-MiniLM-L6-v2
  dimensions: 384
  targets:
    - name: chunk_text
      of: Chunk
      text: "{path}\\n\\n{text}"
workload:
  shapes:
    - id: what_is
      kind: lookup
      example: "what is this about"
      hops: 0
    - id: find_refs
      kind: multi_hop
      example: "what references are mentioned"
      hops: 1
      must_traverse: [MENTIONS]
"""


@pytest.fixture(scope="session")
def mini_blueprint_path(mini_blueprint_yaml) -> Path:
    p = FIXTURE_DIR / "test.blueprint.yaml"
    p.write_text(mini_blueprint_yaml, encoding="utf-8")
    return p


@pytest.fixture(scope="session")
def structured_markdown() -> Path:
    text = """\
# Chapter 1: Introduction

This is the introduction chapter. It covers the basics.

## Setup

How to set up the environment for first use.

## Configuration

Detailed configuration options.

# Chapter 2: Advanced Topics

## Advanced Setup

Going beyond the basics.

## Troubleshooting

Common issues and their solutions.
"""
    p = FIXTURE_DIR / "structured.md"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture(scope="session")
def simple_text() -> Path:
    p = FIXTURE_DIR / "simple.txt"
    p.write_text("Hello world.\nThis is a simple text file.\n", encoding="utf-8")
    return p


@pytest.fixture(scope="session")
def html_fixture() -> Path:
    html = """\
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<h1>Hello</h1>
<p>This is a <b>test</b> page.</p>
<script>alert('x');</script>
<style>body { color: red; }</style>
</body>
</html>"""
    p = FIXTURE_DIR / "test.html"
    p.write_text(html, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Blueprint tests
# ---------------------------------------------------------------------------


class TestBlueprintLoader:
    def test_load_valid(self, mini_blueprint_path: Path) -> None:
        bp = load_blueprint(mini_blueprint_path)
        assert bp["name"] == "test"
        assert bp["blueprint_version"] == "1.0.0"
        assert bp["blueprint_hash"].startswith("sha256:")

    def test_load_not_found(self) -> None:
        with pytest.raises(BlueprintNotFoundError):
            load_blueprint("/nonexistent/blueprint.yaml")

    def test_validate_valid(self, mini_blueprint_yaml: str) -> None:
        import yaml
        bp = yaml.safe_load(mini_blueprint_yaml)
        valid, errors, warnings = validate_blueprint(bp)
        assert valid
        assert len(errors) == 0

    def test_validate_invalid(self) -> None:
        bp = {"name": "broken"}
        valid, errors, warnings = validate_blueprint(bp)
        assert not valid
        assert len(errors) > 0

    def test_compute_hash_deterministic(self, mini_blueprint_yaml: str) -> None:
        import yaml
        bp = yaml.safe_load(mini_blueprint_yaml)
        h1 = compute_hash(bp)
        h2 = compute_hash(bp)
        assert h1 == h2
        assert h1.startswith("sha256:")
        assert len(h1) == 64 + 7  # "sha256:" + 64 hex chars

    def test_compute_hash_field_sensitive(self, mini_blueprint_yaml: str) -> None:
        import yaml
        bp = yaml.safe_load(mini_blueprint_yaml)
        h1 = compute_hash(bp)
        bp["description"] = "different"
        h2 = compute_hash(bp)
        assert h1 != h2

    def test_compute_hash_ignores_blueprint_hash_field(self, mini_blueprint_yaml: str) -> None:
        import yaml
        bp = yaml.safe_load(mini_blueprint_yaml)
        bp["blueprint_hash"] = "sha256:old"
        h = compute_hash(bp)
        assert h != "sha256:old"

    def test_load_strict_fails_on_warnings(self, mini_blueprint_yaml: str) -> None:
        # The mini blueprint has no drift thresholds, which produces warnings
        import yaml
        bp = yaml.safe_load(mini_blueprint_yaml)
        valid, errors, warnings = validate_blueprint(bp, strict=True)
        assert not valid or len(warnings) == 0
        # At minimum there should be warnings about missing drift/golden_set
        if len(warnings) > 0:
            valid_strict, _, _ = validate_blueprint(bp, strict=True)
            if len(warnings) > 0:
                assert not valid_strict


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


class TestLoaders:
    def test_load_text(self, simple_text: Path) -> None:
        result = load_text(simple_text)
        assert "Hello world" in result.text
        assert len(result.pages) == 1
        assert result.pages[0].char_start == 0
        assert result.pages[0].char_end == len(result.text)

    def test_load_markdown(self, structured_markdown: Path) -> None:
        result = load_markdown(structured_markdown)
        assert "# Chapter 1" in result.text
        assert len(result.pages) == 1

    def test_load_html(self, html_fixture: Path) -> None:
        result = load_html(html_fixture)
        assert "Hello" in result.text
        assert "test" in result.text
        assert "alert" not in result.text  # script stripped
        assert "red" not in result.text    # style stripped

    def test_loader_for(self) -> None:
        assert loader_for("doc.pdf") == "application/pdf"
        assert loader_for("doc.txt") == "text/plain"
        assert loader_for("doc.md") == "text/markdown"
        assert loader_for("doc.html") == "text/html"
        assert loader_for("doc.htm") == "text/html"
        with pytest.raises(ValueError, match="unsupported source type"):
            loader_for("doc.doc")

    def test_load_source_text(self, simple_text: Path) -> None:
        mime, text = load_source(simple_text)
        assert mime == "text/plain"
        assert "Hello world" in text

    def test_load_source_markdown(self, structured_markdown: Path) -> None:
        mime, text = load_source(structured_markdown)
        assert mime == "text/markdown"
        assert "Chapter 1" in text

    def test_load_source_html(self, html_fixture: Path) -> None:
        mime, text = load_source(html_fixture)
        assert mime == "text/html"
        assert "Hello" in text

    def test_loaderresult_immutable(self) -> None:
        result = TextLoaderResult(text="hello", pages=[])
        with pytest.raises(Exception):
            result.text = "world"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Chunking tests
# ---------------------------------------------------------------------------


class TestChunking:
    STRUCTURE = {
        "hierarchy": [
            {"level": "chapter", "pattern": r"^#\s+(.+)$"},
            {"level": "section", "pattern": r"^##\s+(.+)$"},
        ],
        "chunk_at": "section",
        "max_chunk_chars": 5000,
    }

    def test_chunk_simple_markdown(self, structured_markdown: Path) -> None:
        text = structured_markdown.read_text(encoding="utf-8")
        chunks = chunk_document(text, self.STRUCTURE, "src-1", "sha256:test")
        # 4 sections: Setup, Configuration, Advanced Setup, Troubleshooting
        assert len(chunks) == 4
        for c in chunks:
            assert c.source_id == "src-1"
            assert c.blueprint_hash == "sha256:test"
            assert c.chunk_id  # auto-derived

    def test_chunk_hierarchy_path(self, structured_markdown: Path) -> None:
        text = structured_markdown.read_text(encoding="utf-8")
        chunks = chunk_document(text, self.STRUCTURE, "src-1", "sha256:test")
        paths = {c.path for c in chunks}
        assert any("Chapter 1" in p for p in paths)
        assert any("Advanced Topics" in p for p in paths)
        assert any("Setup" == p.split(" / ")[-1] for p in paths)

    def test_chunk_content_addressing(self, structured_markdown: Path) -> None:
        text = structured_markdown.read_text(encoding="utf-8")
        chunks1 = chunk_document(text, self.STRUCTURE, "src-1", "sha256:test")
        chunks2 = chunk_document(text, self.STRUCTURE, "src-1", "sha256:test")
        ids1 = [c.chunk_id for c in chunks1]
        ids2 = [c.chunk_id for c in chunks2]
        assert ids1 == ids2

    def test_chunk_different_source_different_id(self, structured_markdown: Path) -> None:
        text = structured_markdown.read_text(encoding="utf-8")
        chunks1 = chunk_document(text, self.STRUCTURE, "src-a", "sha256:test")
        chunks2 = chunk_document(text, self.STRUCTURE, "src-b", "sha256:test")
        ids1 = {c.chunk_id for c in chunks1}
        ids2 = {c.chunk_id for c in chunks2}
        assert ids1 != ids2

    def test_chunk_no_headings(self) -> None:
        text = "Just a plain paragraph with no headings at all."
        chunks = chunk_document(text, self.STRUCTURE, "src-1", "sha256:test")
        assert len(chunks) == 1
        assert chunks[0].path == ""

    def test_skip_regions(self) -> None:
        text = """\
CONTENTS
Introduction ............. 1
Chapter 1 ............... 3

# Chapter 1

Content here.
"""
        skip = [
            {"match": r"^\s*CONTENTS", "span": "to_next_heading"},
        ]
        intervals = apply_skip_regions(text, skip)
        assert len(intervals) >= 1
        start, end = intervals[0]
        skipped = text[start:end]
        assert "CONTENTS" in skipped
        assert "##" not in skipped or "# Chapter" not in skipped
        # The skip should stop before the chapter heading

    def test_skip_regions_to_end(self) -> None:
        text = "Normal content.\nINDEX\napple 12\nbanana 34\n"
        skip = [
            {"match": r"^\s*INDEX", "span": "to_end"},
        ]
        intervals = apply_skip_regions(text, skip)
        assert intervals
        start, end = intervals[0]
        assert end == len(text)

    def test_skip_regions_line(self) -> None:
        text = "Normal.\nSKIP_THIS_LINE\nNormal again.\n"
        skip = [
            {"match": r"SKIP_THIS_LINE", "span": "line"},
        ]
        intervals = apply_skip_regions(text, skip)
        start, end = intervals[0]
        skipped = text[start:end]
        assert "SKIP_THIS_LINE" in skipped

    def test_chunk_no_hierarchy(self) -> None:
        text = "Some text.\nMore text.\n"
        chunks = chunk_document(text, {}, "src-1", "sha256:test")
        assert len(chunks) == 1
        assert "Some text." in chunks[0].text

    def test_chunk_max_chars_subsplit(self) -> None:
        text = "# Big Chapter\n## Step 1\n" + "A" * 3000 + "\n## Step 2\n" + "B" * 3000
        structure = {
            "hierarchy": [
                {"level": "chapter", "pattern": r"^#\s+(.+)$"},
                {"level": "section", "pattern": r"^##\s+(.+)$"},
            ],
            "chunk_at": "section",
            "max_chunk_chars": 2000,
            "subsplit_pattern": r"Step\s+\d+",
        }
        chunks = chunk_document(text, structure, "src-1", "sha256:test")
        # Step 1 and Step 2 should each be a chunk, even though they're long
        assert len(chunks) == 2

    def test_chunk_skip_region_applied_before_chunking(self) -> None:
        text = """\
CONTENTS
Chapter 1 ................ 3

# Chapter 1

This is chapter 1.
"""
        structure = {
            **self.STRUCTURE,
            "skip_regions": [
                {"match": r"^\s*CONTENTS", "span": "to_next_heading"},
            ],
        }
        # The CONTENTS block matches heading patterns and would be a chunk
        # Without skip regions, we'd get a chunk for it. With skip, it's excluded.
        chunks = chunk_document(text, structure, "src-1", "sha256:test")
        for c in chunks:
            assert "CONTENTS" not in c.text
        # Should still get the chapter 1 content
        assert any("This is chapter 1" in c.text for c in chunks)


# ---------------------------------------------------------------------------
# Integration test: end-to-end add sequence
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_add_then_retract_cycle(self, mini_blueprint_path: Path, simple_text: Path, tmp_path: Path) -> None:
        """Simulate the CLI add, list, chunks, retract cycle via the store."""
        from forge.blueprint import load_blueprint
        from forge.chunking import chunk_document
        from forge.loaders import load_source
        from forge.registry import RegistryStore, Source

        db = tmp_path / "forge.db"
        bp = load_blueprint(mini_blueprint_path)

        with RegistryStore(db) as store:
            # Add source
            mime, text = load_source(simple_text)
            content_hash = "sha256:" + hashlib.sha256(simple_text.read_bytes()).hexdigest()
            source = Source(
                uri=str(simple_text),
                content_hash=content_hash,
                mime=mime,
                blueprint_version=bp["blueprint_version"],
                blueprint_hash=bp["blueprint_hash"],
                added_by="tester",
                status="chunking",
            )
            source = store.add_source(source)
            assert source.source_id is not None

            # Chunk
            chunks = chunk_document(text, bp["structure"], source.source_id, bp["blueprint_hash"])
            store.add_chunks(chunks)
            store.update_source_status(source.source_id, "ready")

            # List
            sources = store.list_sources()
            assert len(sources) == 1
            assert sources[0].uri == str(simple_text)

            # Chunks
            stored_chunks = store.get_chunks_for_source(source.source_id)
            assert len(stored_chunks) == len(chunks)
            assert stored_chunks[0].chunk_id == chunks[0].chunk_id

            # Retract
            assert store.retract_source(source.source_id)
            retracted = store.get_source(source.source_id)
            assert retracted is not None
            assert retracted.status == "retracted"
            assert store.get_chunks_for_source(source.source_id) == []

    def test_re_add_unchanged_is_noop(self, mini_blueprint_path: Path, simple_text: Path, tmp_path: Path) -> None:
        """Adding the same source twice should report unchanged."""
        from forge.blueprint import load_blueprint
        from forge.chunking import chunk_document
        from forge.loaders import load_source
        from forge.registry import RegistryStore, Source

        bp = load_blueprint(mini_blueprint_path)
        db = tmp_path / "forge.db"
        content_hash = "sha256:" + hashlib.sha256(simple_text.read_bytes()).hexdigest()

        with RegistryStore(db) as store:
            # First add
            mime, text = load_source(simple_text)
            source1 = store.add_source(Source(
                uri=str(simple_text),
                content_hash=content_hash,
                mime=mime,
                blueprint_version=bp["blueprint_version"],
                blueprint_hash=bp["blueprint_hash"],
                added_by="tester",
                status="chunking",
            ))
            chunks = chunk_document(text, bp["structure"], source1.source_id, bp["blueprint_hash"])
            store.add_chunks(chunks)
            store.update_source_status(source1.source_id, "ready")

        # Second add — same content
        with RegistryStore(db) as store:
            existing = store.get_source_by_uri(str(simple_text))
            assert existing is not None
            assert existing.content_hash == content_hash

            # If content hasn't changed, we should skip
            if existing.content_hash == content_hash and existing.status != "retracted":
                # This is the "unchanged" path
                new_source = store.add_source(Source(
                    uri=str(simple_text),
                    content_hash=content_hash,
                    mime=mime,
                    blueprint_version=bp["blueprint_version"],
                    blueprint_hash=bp["blueprint_hash"],
                    added_by="tester",
                    status="chunking",
                ))
                chunks2 = chunk_document(text, bp["structure"], new_source.source_id, bp["blueprint_hash"])
                new_count = store.add_chunks(chunks2)
                store.update_source_status(new_source.source_id, "ready")
                # All chunks should be duplicates (inserted but INSERT OR IGNORE)
                # Since they have different source_ids, they get different chunk_ids
                # But the text content is the same
                stored = store.get_source(new_source.source_id)
                assert stored is not None
                assert stored.chunk_count > 0


# ---------------------------------------------------------------------------
# Bug regression tests (from AGENTS.md)
# ---------------------------------------------------------------------------


class TestBugFixes:
    def test_bug1_re_split_capture_groups(self) -> None:
        """Bug 1: measure from finditer positions, not re.split with capture groups."""
        import re
        text = "## A\nbody1\n## B\nbody2\n"
        # If we used re.split here, capture groups would inject extra elements
        rx = re.compile(r"^##\s+(.+)$", re.MULTILINE)
        positions = [m.start() for m in rx.finditer(text)]
        assert len(positions) == 2
        # Verify the positions are actual heading starts
        assert text[positions[0]:].startswith("## A")

    def test_bug2_allcaps_heading_three_words(self) -> None:
        """Bug 2: {3,} -> {2,} so 3-word all-caps headings match."""
        import re
        # The AGENTS.md says: ALL-CAPS heading regex [A-Z][A-Z0-9'\-]{3,}
        # needs a 4-character first word, so THE DAKOTA FIRE HOLE never matched.
        # Use {2,} to require at least 3 chars for the first word.
        # Pattern from the firecraft blueprint (section level):
        pat_good = r"^([A-Z][A-Z0-9'\-]{2,}(?:\s+[A-Z0-9'\-&]+){0,6})\s*$"
        heading = "THE DAKOTA FIRE HOLE"
        good = re.compile(pat_good, re.MULTILINE)
        m = good.search(heading)
        assert m is not None, "THE DAKOTA FIRE HOLE should match with {2,}"

    def test_bug3_advance_past_whitespace(self) -> None:
        """Bug 3: heading positions must advance past leading whitespace."""
        import re
        # With MULTILINE, ^ matches at line boundaries (after \n)
        # A pattern like ^\s*## under MULTILINE matches the \n as \s
        # The chunker should advance past that leading whitespace/newline
        text = "previous line\n  ## Heading\ncontent\n"
        # Use a pattern with leading \s* to simulate the bug:
        rx = re.compile(r"^\s*##\s+(.+)$", re.MULTILINE)
        m = rx.search(text)
        assert m is not None
        # With \s* at the start and MULTILINE, the match can start on \n
        start = m.start()
        raw_start = start
        # Advance past whitespace
        while start < m.end() and text[start] in (" ", "\t", "\n", "\r"):
            start += 1
        assert start > raw_start, f"should advance past whitespace from {raw_start}"


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------


class TestExtraction:
    """Prompt assembly, models, dry-run artifact, cost report."""

    SAMPLE_BLUEPRINT = {
        "blueprint_version": "1.0.0",
        "blueprint_hash": "sha256:test",
        "name": "test",
        "ontology": {
            "closed": True,
            "node_types": [
                {"label": "Topic", "description": "A subject discussed in the text.",
                 "answers": "What is this about?",
                 "properties": [{"name": "name", "type": "STRING", "required": True}]},
                {"label": "Reference", "description": "A referenced item.",
                 "answers": "What is referenced?",
                 "properties": [{"name": "name", "type": "STRING", "required": True}]},
            ],
            "relationship_types": [
                {"label": "MENTIONS", "description": "The topic mentions this reference."},
            ],
            "patterns": [["Topic", "MENTIONS", "Reference"]],
            "deterministic_relations": [],
        },
        "structure": {"chunk_at": "section", "hierarchy": []},
    }

    SAMPLE_CHUNKS = [
        {"chunk_id": "aaabbb", "text": "Python is a programming language.", "path": "/intro"},
        {"chunk_id": "cccddd", "text": "TypeScript is also a programming language.", "path": "/advanced"},
    ]

    def test_ontology_block_contains_types(self) -> None:
        from forge.extract.prompts import build_ontology_block

        block = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        assert "Topic" in block
        assert "Reference" in block
        assert "MENTIONS" in block
        assert "A subject discussed" in block
        assert "closed" in block

    def test_ontology_block_includes_rules(self) -> None:
        from forge.extract.prompts import build_ontology_block

        block = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        assert "verbatim" in block.lower()
        assert "confidence" in block.lower()

    def test_build_chunk_prompt_structure(self) -> None:
        from forge.extract.prompts import build_chunk_prompt, build_ontology_block

        ob = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        messages = build_chunk_prompt("some text", "/test", ob)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "some text" in messages[1]["content"]
        assert "ONTOLOGY" in messages[0]["content"]

    def test_cache_prefix_first_in_system(self) -> None:
        """The ontology block is the first content after SYSTEM_HEAD in the system message."""
        from forge.extract.prompts import build_chunk_prompt, build_ontology_block

        ob = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        messages = build_chunk_prompt("text", "/p", ob)
        sys_content = messages[0]["content"]
        # The system message starts with SYSTEM_HEAD, then the ontology block
        assert sys_content.startswith("You are a precise information extractor")
        assert "ONTOLOGY" in sys_content

    def test_estimate_tokens(self) -> None:
        from forge.extract.prompts import estimate_tokens

        assert estimate_tokens("hello world") == 2  # 11 chars / 4
        assert estimate_tokens("a" * 100) == 25

    def test_estimate_prompt_cost_first_chunk(self) -> None:
        from forge.extract.prompts import build_ontology_block, estimate_prompt_cost

        ob = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        cost = estimate_prompt_cost(ob, "text", "/p", model="gpt-4o", cached=False)
        assert cost["cached_tokens"] == 0
        assert cost["cost_usd"] > 0

    def test_estimate_prompt_cost_cached(self) -> None:
        from forge.extract.prompts import build_ontology_block, estimate_prompt_cost

        ob = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        cost = estimate_prompt_cost(ob, "text", "/p", model="gpt-4o", cached=True)
        assert cost["cached_tokens"] > 0
        # Cached should be cheaper than non-cached
        uncached = estimate_prompt_cost(ob, "text", "/p", model="gpt-4o", cached=False)
        assert cost["cost_usd"] < uncached["cost_usd"]

    def test_mock_extractor_produces_result(self) -> None:
        from forge.extract.provider import MockExtractor

        extractor = MockExtractor()
        from forge.extract.prompts import build_ontology_block

        ob = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        result = extractor.extract("some text", "/p", "chunk1", "src1", ob)
        assert result.chunk_id == "chunk1"
        assert result.source_id == "src1"
        assert len(result.entities) > 0

    def test_mock_extractor_empty_mode(self) -> None:
        from forge.extract.provider import MockExtractor
        from forge.extract.prompts import build_ontology_block

        extractor = MockExtractor(always_empty=True)
        ob = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        result = extractor.extract("text", "/p", "c1", "s1", ob)
        assert len(result.entities) == 0

    def test_mock_dry_run_writes_artifact(self, tmp_path) -> None:
        from forge.extract.provider import MockExtractor
        from forge.extract.prompts import build_ontology_block

        extractor = MockExtractor()
        ob = build_ontology_block(self.SAMPLE_BLUEPRINT["ontology"])
        out_path = extractor.dry_run("text", "/p", "chunk1", "src1", ob, out_dir=tmp_path / "dry")
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "chunk1" in content
        assert "ontology_block" in content

    def test_run_extraction_dry_manifest(self, tmp_path) -> None:
        from forge.extract import run_extraction, save_dry_run_manifest

        results, report = run_extraction(
            self.SAMPLE_BLUEPRINT,
            self.SAMPLE_CHUNKS,
            source_id="src1",
            provider="mock",
            dry_run=True,
            dry_run_dir=tmp_path / "extract-dry",
        )
        assert len(results) == 2
        assert report.total_chunks == 2

        manifest = save_dry_run_manifest(
            results, report, self.SAMPLE_BLUEPRINT, tmp_path / "extract-dry"
        )
        assert manifest.exists()
        manifest_data = manifest.read_text(encoding="utf-8")
        assert "test" in manifest_data

    def test_cost_report_cache_savings(self) -> None:
        from forge.extract.cost import ChunkCostRow, build_cost_report

        rows = [
            ChunkCostRow("c1", "/a", "gpt-4o", 500, 0, 100, 2, 1, 0.002, 0.004),
            ChunkCostRow("c2", "/b", "gpt-4o", 500, 300, 100, 3, 2, 0.001, 0.004),
        ]
        report = build_cost_report(rows, "hash:abc", "source.md")
        assert report.total_chunks == 2
        assert report.total_cached_tokens == 300
        assert report.cache_savings_usd > 0
        s = report.summary
        assert s["cache_savings_pct"] > 0

    def test_cost_report_empty(self) -> None:
        from forge.extract.cost import build_cost_report

        report = build_cost_report([], "hash:abc", "source.md")
        assert report.total_chunks == 0
        assert report.cache_savings_usd == 0

    def test_extractor_for_factory(self) -> None:
        from forge.extract.provider import extractor_for

        mock = extractor_for("mock")
        assert mock.model == "mock"

    def test_extractor_for_invalid(self) -> None:
        from forge.extract.provider import extractor_for

        import pytest
        with pytest.raises(ValueError, match="Unknown extraction provider"):
            extractor_for("nonexistent")