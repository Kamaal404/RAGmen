"""Lattice Agent CLI — answer questions from the command line.

Connects to a store (memory or Neo4j) and a blueprint, then answers questions.

Usage::

    lattice-agent ask "what is a dakota fire hole" --store memory --blueprint bp.yaml
    lattice-agent ask "what materials do I need" --store neo4j
    lattice-agent interactive --store memory
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _open_store(backend: str, **kwargs: Any) -> Any:
    if backend == "neo4j":
        try:
            from neo4j import GraphDatabase
        except ImportError:
            sys.exit("neo4j driver not installed. Run: pip install neo4j")

        driver = GraphDatabase.driver(
            kwargs.get("uri", "bolt://localhost:7687"),
            auth=(kwargs.get("username", "neo4j"), kwargs.get("password", "")),
        )
        return _Neo4jHandle(driver, kwargs.get("database", "neo4j"))
    else:
        # Try to load from forge.db if it exists
        store_path = kwargs.get("store_path", Path.cwd() / "forge.db")
        if Path(store_path).exists():
            try:
                from forge.registry.store import RegistryStore
                from forge.store import MemoryStore

                memory = MemoryStore()
                reg = RegistryStore(store_path)
                for s in reg.list_sources():
                    if s.status == "retracted":
                        continue
                    chks = reg.get_chunks_for_source(s.source_id)
                    for c in chks:
                        memory.write_chunk(c, s)
                reg.close()
                return memory
            except Exception:
                pass
        from forge.store import MemoryStore

        return MemoryStore()


class _Neo4jHandle:
    def __init__(self, driver: Any, database: str) -> None:
        self._driver = driver
        self._database = database

    def close(self) -> None:
        self._driver.close()


def _load_blueprint(path: str | None) -> dict:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def cmd_ask(args: Any) -> None:
    from agent.answers import Agent, format_answer

    blueprint = _load_blueprint(args.blueprint)
    store = _open_store(
        args.store,
        store_path=args.store_path,
        uri=args.neo4j_uri,
        username=args.neo4j_user,
        password=args.neo4j_password,
    )

    agent = Agent(store, blueprint)
    answer = agent.answer(args.question, top_k=args.top_k, hops=args.hops)
    print(format_answer(answer, blueprint.get("name", "")))
    store.close() if hasattr(store, "close") else None


def cmd_interactive(args: Any) -> None:
    from agent.answers import Agent, format_answer

    blueprint = _load_blueprint(args.blueprint)
    store = _open_store(args.store)

    agent = Agent(store, blueprint)
    bp_name = blueprint.get("name", "unnamed")
    print(f"Lattice Agent (corpus: {bp_name})")
    print("Type 'exit' or 'quit' to stop.")
    print()

    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit"):
            break
        answer = agent.answer(q, top_k=args.top_k, hops=args.hops)
        print(format_answer(answer, bp_name))
        print()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Lattice Agent — answer questions from the corpus")
    ap.add_argument("--store", choices=["memory", "neo4j"], default="memory")
    ap.add_argument("--blueprint", "-b", help="Blueprint YAML")
    ap.add_argument("--store-path", help="Path to forge.db (memory mode)")
    ap.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default="")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--hops", type=int, default=1)

    sub = ap.add_subparsers(dest="command", required=True)

    ask_p = sub.add_parser("ask", help="Answer one question")
    ask_p.add_argument("question", help="Natural language question")

    interactive_p = sub.add_parser("interactive", help="Interactive Q&A session")

    args = ap.parse_args()
    commands = {"ask": cmd_ask, "interactive": cmd_interactive}
    commands[args.command](args)


if __name__ == "__main__":
    main()