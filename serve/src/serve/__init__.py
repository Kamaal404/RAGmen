"""Serve — read-only MCP retrieval server for Lattice.

Hybrid search (vector + fulltext + graph traversal), neighbourhood expansion,
citation lookup, schema introspection, and guardrail enforcement.

Connects to either the in-memory store (testing) or Neo4j (production).
"""