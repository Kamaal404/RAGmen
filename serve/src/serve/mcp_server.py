# ruff: noqa: E501
"""Lattice Serve — read-only MCP retrieval server.

Tools:
  - search(query, top_k, hops, filters)  — hybrid retrieval
  - expand(node_id, hops, top_k)         — neighbourhood expansion
  - cite(chunk_id)                       — verbatim span + source + page
  - explain_path(a, b)                   — how two entities connect
  - schema()                             — active blueprint ontology

Run::

    lattice-serve --store memory [--blueprint blueprint.yaml]
    lattice-serve --store neo4j --neo4j-uri bolt://... [--blueprint blueprint.yaml]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from serve.filters import FilterContext, GuardrailCompiler
from serve.retrievers import HybridRetriever, SearchResult


# ---------------------------------------------------------------------------
# Store setup
# ---------------------------------------------------------------------------


def _load_blueprint(path: str | None) -> dict:
    """Load a blueprint from path or return an empty one."""
    if path is None:
        return {"name": "unknown", "ontology": {}, "guardrails": {}}
    p = Path(path)
    if not p.exists():
        return {"name": "unknown", "ontology": {}, "guardrails": {}}
    try:
        import yaml
        raw = p.read_text(encoding="utf-8")
        return yaml.safe_load(raw) or {}
    except Exception:
        return {"name": "unknown", "ontology": {}, "guardrails": {}}


def _open_store(backend: str, blueprint: dict, **kwargs: Any) -> Any:
    """Open a store (memory or Neo4j) and optionally seed it from a forge db."""
    if backend == "neo4j":
        # Lazy import to avoid neo4j dependency unless needed
        try:
            from neo4j import GraphDatabase
        except ImportError:
            sys.exit("neo4j driver not installed. Run: pip install neo4j")

        driver = GraphDatabase.driver(
            kwargs.get("uri", "bolt://localhost:7687"),
            auth=(kwargs.get("username", "neo4j"), kwargs.get("password", "")),
        )
        return _Neo4jStore(driver, kwargs.get("database", "neo4j"), blueprint)
    else:
        try:
            from forge.store import MemoryStore
        except ImportError:
            sys.exit(
                "MemoryStore requires lattice-forge. "
                "Run: pip install 'lattice-serve[memory]'"
            )
        return MemoryStore()


class _Neo4jStore:
    """Thin wrapper to make Neo4j look like a searchable store for Serve."""

    def __init__(self, driver: Any, database: str, blueprint: dict) -> None:
        self._driver = driver
        self._database = database
        self._blueprint = blueprint

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        with self._driver.session(database=self._database) as session:
            result = session.run(cypher, params or {})
            return [dict(r) for r in result]

    def close(self) -> None:
        self._driver.close()


# ---------------------------------------------------------------------------
# Lattice Serve server
# ---------------------------------------------------------------------------


def _make_tool_list(retriever: Any) -> list[Tool]:
    return [
        Tool(
            name="search",
            description="Hybrid search across the knowledge graph. "
                        "Combines vector similarity, keyword match, and graph traversal. "
                        "Use for any question about the corpus content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query"},
                    "top_k": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                    "hops": {"type": "integer", "description": "Graph traversal depth (default 1)", "default": 1},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="expand",
            description="Explore the neighbourhood of a chunk or entity. "
                        "Finds entities mentioned in the chunk, then their other chunks. "
                        "Use to discover related content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Chunk ID or entity ID"},
                    "hops": {"type": "integer", "description": "Traversal depth (default 1)", "default": 1},
                    "top_k": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                },
                "required": ["node_id"],
            },
        ),
        Tool(
            name="cite",
            description="Get the verbatim text, source URI, and page number for a chunk. "
                        "Use to cite evidence for a claim.",
            inputSchema={
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string", "description": "Chunk identifier"},
                },
                "required": ["chunk_id"],
            },
        ),
        Tool(
            name="explain_path",
            description="Explain how two entities are connected in the graph. "
                        "Returns the path of relationships between them.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_label": {"type": "string", "description": "Entity label (e.g. Procedure)"},
                    "source_name": {"type": "string", "description": "Entity name (e.g. teepee)"},
                    "target_label": {"type": "string", "description": "Entity label"},
                    "target_name": {"type": "string", "description": "Entity name"},
                    "max_depth": {"type": "integer", "description": "Max path length (default 4)", "default": 4},
                },
                "required": ["source_label", "source_name", "target_label", "target_name"],
            },
        ),
        Tool(
            name="schema",
            description="Get the active blueprint schema — node types, relationship types, "
                        "and allowed patterns. Use to understand what the graph contains.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


def _format_search_result(result: SearchResult) -> str:
    lines: list[str] = [
        f"Search results for: {result.query}",
        f"Retriever: {result.retriever}  |  Total: {result.total_hits}",
    ]
    for i, hit in enumerate(result.hits):
        lines.append("")
        lines.append(f"[{i + 1}] score={hit.score}  chunk={hit.chunk_id[:16]}...")
        lines.append(f"    path: {hit.path}")
        if hit.source_id:
            lines.append(f"    source: {hit.source_id}")
        if hit.page:
            lines.append(f"    page: {hit.page}")
        if hit.entity_names:
            names = hit.entity_names[:5]
            lines.append(f"    entities: {', '.join(names)}")
        lines.append(f"    text: {hit.text[:200]}")
    return "\n".join(lines)


def _format_cite(chunk_id: str, store: Any) -> str:
    chunk = store.get_chunk(chunk_id)
    if chunk is None:
        return f"chunk not found: {chunk_id}"

    source = store.get_source(chunk.source_id) if hasattr(store, "get_source") else None
    lines: list[str] = [
        f"Chunk: {chunk_id}",
        f"Path: {chunk.path}",
        f"Source: {getattr(source, 'uri', chunk.source_id) if source else chunk.source_id}",
        f"Page: {chunk.page or 'N/A'}",
        f"Characters: [{chunk.char_start}, {chunk.char_end})",
        f"Blueprint: {chunk.blueprint_hash[:16]}...",
        "",
        chunk.text,
    ]
    return "\n".join(lines)


def _format_explain_path(
    label_a: str, name_a: str, label_b: str, name_b: str,
    store: Any,
    max_depth: int = 4,
) -> str:
    """In MemoryStore, find paths by scanning relations."""
    if not hasattr(store, "relations"):
        return "Path explanation not available (Neo4j mode with no direct relation index)."

    # Build adjacency
    adj: dict[str, list[dict]] = {}
    for rel in store.relations:
        src_key = f"{rel['source_label']}\0{rel['source_name']}"
        tgt_key = f"{rel['target_label']}\0{rel['target_name']}"
        label = rel["relation"]
        adj.setdefault(src_key, []).append({"to": tgt_key, "rel": label, "data": rel})
        # Bidirectional for path finding
        adj.setdefault(tgt_key, []).append({"to": src_key, "rel": label, "data": rel})

    start = f"{label_a}\0{name_a}"
    target = f"{label_b}\0{name_b}"

    if start not in adj or target not in adj:
        return f"No path found: {name_a} ({label_a}) has no connections."

    # BFS
    visited: set[str] = {start}
    queue: list[list[dict]] = [[{"key": start, "rel": None}]]
    while queue:
        path = queue.pop(0)
        last = path[-1]
        if last["key"] == target:
            return _format_path(path)
        if len(path) >= max_depth:
            continue
        for edge in adj.get(last["key"], []):
            if edge["to"] not in visited:
                visited.add(edge["to"])
                queue.append(path + [{"key": edge["to"], "rel": edge["rel"], "data": edge["data"]}])

    return f"No path found between {name_a} ({label_a}) and {name_b} ({label_b}) within {max_depth} hops."


def _format_path(path: list[dict]) -> str:
    lines: list[str] = ["Path found:"]
    for i, step in enumerate(path):
        label_name = step["key"].replace("\0", " : ")
        if step.get("rel"):
            lines.append(f"  [{i}] {label_name}")
        else:
            lines.append(f"  [{i}] {label_name}  (start)")
        if step.get("rel"):
            edge = step.get("data", {})
            quote = edge.get("quote", "")
            if quote:
                lines.append(f"       └─ {step['rel']} \"{quote[:80]}\"")
            else:
                lines.append(f"       └─ {step['rel']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server(
    store: Any,
    blueprint: dict | None = None,
) -> Server:
    """Create a Lattice Serve MCP server."""
    bp = blueprint or {}
    retriever = HybridRetriever(store)
    guardrails = GuardrailCompiler(bp)
    compiled_filters = guardrails.compile()

    app = Server("lattice-serve")

    tool_list = _make_tool_list(retriever)

    async def list_tools() -> list[Tool]:
        return tool_list

    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "search":
            query = arguments.get("query", "")
            top_k = arguments.get("top_k", 10)
            hops = arguments.get("hops", 1)
            result = retriever.retrieve(query, top_k=top_k, hops=hops)
            # Apply guardrails
            ctx = FilterContext()
            result_hits = guardrails.apply(compiled_filters, result.hits, ctx)
            result = SearchResult(
                query=result.query,
                hits=result_hits,
                total_hits=len(result_hits),
                retriever=result.retriever,
            )
            return [TextContent(type="text", text=_format_search_result(result))]

        if name == "expand":
            node_id = arguments.get("node_id", "")
            hops = arguments.get("hops", 1)
            top_k = arguments.get("top_k", 20)
            result = retriever.expand(node_id, hops=hops, top_k=top_k)
            text = _format_search_result(result)
            return [TextContent(type="text", text=text)]

        if name == "cite":
            chunk_id = arguments.get("chunk_id", "")
            text = _format_cite(chunk_id, store)
            return [TextContent(type="text", text=text)]

        if name == "explain_path":
            source_label = arguments.get("source_label", "")
            source_name = arguments.get("source_name", "")
            target_label = arguments.get("target_label", "")
            target_name = arguments.get("target_name", "")
            max_depth = arguments.get("max_depth", 4)
            text = _format_explain_path(
                source_label, source_name,
                target_label, target_name,
                store,
                max_depth=max_depth,
            )
            return [TextContent(type="text", text=text)]

        if name == "schema":
            ont = bp.get("ontology", {})
            text = json.dumps(ont, indent=2)
            return [TextContent(type="text", text=text)]

        raise ValueError(f"Unknown tool: {name}")

    app.list_tools = list_tools  # type: ignore[method-assign]
    app.call_tool = call_tool  # type: ignore[method-assign]

    return app


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Lattice Serve — MCP retrieval server")
    ap.add_argument("--store", choices=["memory", "neo4j"], default="memory")
    ap.add_argument("--blueprint", "-b", help="Blueprint YAML path")
    ap.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default="")

    args = ap.parse_args()

    bp = _load_blueprint(args.blueprint)
    store = _open_store(args.store, bp,
                        uri=args.neo4j_uri,
                        username=args.neo4j_user,
                        password=args.neo4j_password)

    app = create_server(store, bp)
    print(f"Lattice Serve starting (store={args.store})", file=sys.stderr)

    import asyncio
    asyncio.run(stdio_server(app))


if __name__ == "__main__":
    main()


__all__ = ["create_server", "main"]