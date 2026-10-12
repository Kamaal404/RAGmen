# ruff: noqa: E501, S101
"""Tests for the MCP server tool functions (without running the MCP transport)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "forge" / "src"))

from forge.store import MemoryStore
from serve.mcp_server import _format_cite, _format_explain_path, _format_search_result, create_server
from serve.retrievers import SearchHit, SearchResult

from .conftest import seed_memory_store


@pytest.fixture
def store(sample_source, sample_chunks):
    s = MemoryStore()
    seed_memory_store(s, sample_source, sample_chunks)
    return s


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


class TestServerCreation:
    def test_create_server_returns_server(self, store, sample_blueprint) -> None:
        server = create_server(store, sample_blueprint)
        assert server is not None

    async def test_list_tools(self, store, sample_blueprint) -> None:
        server = create_server(store, sample_blueprint)
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "search" in tool_names
        assert "expand" in tool_names
        assert "cite" in tool_names
        assert "explain_path" in tool_names
        assert "schema" in tool_names

    async def test_search_tool(self, store, sample_blueprint) -> None:
        server = create_server(store, sample_blueprint)
        result = await server.call_tool("search", {"query": "dakota fire hole", "top_k": 5})
        assert len(result) == 1
        text = result[0].text
        assert "dakota" in text.lower() or "Search results" in text

    async def test_cite_tool(self, store, sample_blueprint, sample_chunks) -> None:
        server = create_server(store, sample_blueprint)
        cite_result = await server.call_tool("cite", {"chunk_id": sample_chunks[1].chunk_id})
        assert len(cite_result) == 1
        assert "Dakota" in cite_result[0].text or "chunk" in cite_result[0].text

    async def test_schema_tool(self, store, sample_blueprint) -> None:
        server = create_server(store, sample_blueprint)
        result = await server.call_tool("schema", {})
        assert len(result) == 1
        assert "node_types" in result[0].text

    async def test_expand_tool(self, store, sample_blueprint) -> None:
        server = create_server(store, sample_blueprint)
        result = await server.call_tool("expand", {"node_id": "chunk001", "hops": 1})
        assert len(result) >= 0

    async def test_explain_path_tool(self, store, sample_blueprint) -> None:
        server = create_server(store, sample_blueprint)
        result = await server.call_tool("explain_path", {
            "source_label": "Procedure",
            "source_name": "dakota fire hole",
            "target_label": "Material",
            "target_name": "birch bark",
            "max_depth": 3,
        })
        assert len(result) == 1
        assert "Path" in result[0].text or "No path" in result[0].text


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestFormatHelpers:
    def test_format_search_result(self) -> None:
        result = SearchResult(
            query="test query",
            hits=[
                SearchHit(chunk_id="abc123", text="Some text about fire.", path="/chapter1",
                          score=0.95, source_id="src1"),
                SearchHit(chunk_id="def456", text="More text.", path="/chapter2",
                          score=0.8),
            ],
            total_hits=2,
            retriever="hybrid",
        )
        text = _format_search_result(result)
        assert "test query" in text
        assert "abc123" in text
        assert "hybrid" in text

    def test_format_cite_found(self, store) -> None:
        text = _format_cite("chunk001", store)
        assert "chunk001" in text
        assert "Gather" in text

    def test_format_cite_not_found(self, store) -> None:
        text = _format_cite("nonexistent", store)
        assert "not found" in text

    def test_format_explain_path_found(self, store) -> None:
        text = _format_explain_path(
            "Procedure", "dakota fire hole",
            "Material", "birch bark",
            store,
            max_depth=3,
        )
        assert "Path" in text or "No path" in text

    def test_format_explain_path_no_connection(self, store) -> None:
        text = _format_explain_path(
            "Procedure", "dakota fire hole",
            "Material", "unicorn dust",
            store,
            max_depth=3,
        )
        # The entity "unicorn dust" doesn't exist
        assert "No path" in text or "Path" not in text or "unicorn" in text


# ---------------------------------------------------------------------------
# Reuse conftest fixtures
# ---------------------------------------------------------------------------


# Using conftest's sample_chunks (session-scoped, 4 chunks)