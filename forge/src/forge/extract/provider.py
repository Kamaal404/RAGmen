"""Extractor providers — interface, dry-run mock, and OpenAI batch.

The interface is the only thing the orchestrator depends on. New providers
(Anthropic, Ollama, local) implement the same protocol.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from forge.extract.models import ExtractionResult, ExtractedEntity, ExtractedRelation, ExtractedProperty


# ---------------------------------------------------------------------------
# Extractor protocol
# ---------------------------------------------------------------------------

class Extractor(ABC):
    """Protocol for an extraction provider.

    Each call extracts from one chunk. The ontology_block is the cache prefix,
    byte-identical across every chunk in a run.
    """

    model: str

    @abstractmethod
    def extract(
        self,
        chunk_text: str,
        chunk_path: str,
        chunk_id: str,
        source_id: str,
        ontology_block: str,
        is_first_chunk: bool = False,
    ) -> ExtractionResult:
        """Extract entities and relations from one chunk.

        *is_first_chunk* being True means the ontology block will NOT be
        prompt-cached (it's the first time the cache prefix is seen).
        Providers use this to compute accurate cost estimates.
        """

    @abstractmethod
    def dry_run(
        self,
        chunk_text: str,
        chunk_path: str,
        chunk_id: str,
        source_id: str,
        ontology_block: str,
        *,
        out_dir: str | Path,
    ) -> Path:
        """Write the extraction prompt to disk without calling any LLM.

        Returns the path to the written artifact.
        """


# ---------------------------------------------------------------------------
# Mock / dry-run provider
# ---------------------------------------------------------------------------

class MockExtractor(Extractor):
    """Mock extractor for offline testing and dry-run prompt inspection.

    Produces a fake extraction result for every chunk. No LLM called.
    Use this to verify the pipeline without API keys.
    """

    model = "mock"

    def __init__(self, always_empty: bool = False) -> None:
        self.always_empty = always_empty

    def extract(
        self,
        chunk_text: str,
        chunk_path: str,
        chunk_id: str,
        source_id: str,
        ontology_block: str,
        is_first_chunk: bool = False,
    ) -> ExtractionResult:
        if self.always_empty:
            return ExtractionResult(chunk_id=chunk_id, source_id=source_id)

        return ExtractionResult(
            chunk_id=chunk_id,
            source_id=source_id,
            entities=[
                ExtractedEntity(
                    label="Topic",
                    name=chunk_path or "Untitled",
                    quote=chunk_text[:80],
                    char_start=0,
                    char_end=min(80, len(chunk_text)),
                ),
            ],
            unmapped_mentions=[],
            prompt_tokens=len(ontology_block) // 4 + len(chunk_text) // 4,
            completion_tokens=100,
            cached_prompt_tokens=0 if is_first_chunk else len(ontology_block) // 4,
            cost_usd=0.0,
            model="mock",
        )

    def dry_run(
        self,
        chunk_text: str,
        chunk_path: str,
        chunk_id: str,
        source_id: str,
        ontology_block: str,
        *,
        out_dir: str | Path,
    ) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        artifact = {
            "chunk_id": chunk_id,
            "source_id": source_id,
            "path": chunk_path,
            "ontology_block": ontology_block,
            "chunk_text": chunk_text,
            "system_prompt": "## ONTOLOGY (closed)\n" + ontology_block,
        }
        path = out / f"{chunk_id[:16]}.dry-run.json"
        path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# OpenAI batch provider
# ---------------------------------------------------------------------------

class OpenAIExtractor(Extractor):
    """Extraction through the OpenAI API.

    Uses the chat completions endpoint. When *batch* is True (default), the
    caller should collect prompts and send them via the Batch API for
    cost savings (~50% reduction). Single-shot extraction is supported for
    interactive use.

    Requires ``OPENAI_API_KEY`` in the environment.
    """

    model = "gpt-4o"

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        batch: bool = True,
    ) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._batch = batch

    def extract(
        self,
        chunk_text: str,
        chunk_path: str,
        chunk_id: str,
        source_id: str,
        ontology_block: str,
        is_first_chunk: bool = False,
    ) -> ExtractionResult:
        if not self._api_key:
            return ExtractionResult(
                chunk_id=chunk_id,
                source_id=source_id,
                model=self.model,
                entities=[],
                relations=[],
                unmapped_mentions=["No API key configured. Set OPENAI_API_KEY."],
            )

        from forge.extract.prompts import build_chunk_prompt

        messages = build_chunk_prompt(chunk_text, chunk_path, ontology_block)
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self._api_key)
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
        except ImportError:
            return ExtractionResult(
                chunk_id=chunk_id,
                source_id=source_id,
                model=self.model,
                unmapped_mentions=["openai package not installed. Run: pip install openai"],
            )

        choice = resp.choices[0]
        usage = resp.usage
        raw = choice.message.content or "{}"

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}

        entities = [
            ExtractedEntity(
                label=e["label"],
                name=e["name"],
                properties=[ExtractedProperty(k, v) for k, v in e.get("properties", {}).items()],
                quote=e.get("quote", ""),
                char_start=e.get("char_start", 0),
                char_end=e.get("char_end", 0),
                confidence=e.get("confidence", 1.0),
            )
            for e in data.get("entities", [])
        ]

        relations = [
            ExtractedRelation(
                source_label=r["source_label"],
                source_name=r["source_name"],
                relation=r["relation"],
                target_label=r["target_label"],
                target_name=r["target_name"],
                properties=[ExtractedProperty(k, v) for k, v in r.get("properties", {}).items()],
                quote=r.get("quote", ""),
                char_start=r.get("char_start", 0),
                char_end=r.get("char_end", 0),
                confidence=r.get("confidence", 1.0),
            )
            for r in data.get("relations", [])
        ]

        cached_tokens = 0
        if usage is not None:
            prompt_tokens = usage.prompt_tokens if hasattr(usage, "prompt_tokens") else 0
            completion_tokens = usage.completion_tokens if hasattr(usage, "completion_tokens") else 0
            cached_tokens = (
                usage.prompt_tokens_details.cached_tokens
                if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details
                else 0
            )
        else:
            prompt_tokens = 0
            completion_tokens = 0

        return ExtractionResult(
            chunk_id=chunk_id,
            source_id=source_id,
            entities=entities,
            relations=relations,
            unmapped_mentions=data.get("unmapped_mentions", []),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_tokens,
            model=self.model,
        )

    def dry_run(
        self,
        chunk_text: str,
        chunk_path: str,
        chunk_id: str,
        source_id: str,
        ontology_block: str,
        *,
        out_dir: str | Path,
    ) -> Path:
        from forge.extract.prompts import build_chunk_prompt

        messages = build_chunk_prompt(chunk_text, chunk_path, ontology_block)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        artifact = {
            "chunk_id": chunk_id,
            "source_id": source_id,
            "path": chunk_path,
            "model": self.model,
            "messages": messages,
        }
        path = out / f"{chunk_id[:16]}.dry-run.json"
        path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        return path


def extractor_for(provider: str = "mock", **kwargs: Any) -> Extractor:
    """Factory: return the right Extractor for the provider name."""
    _PROVIDERS: dict[str, type[Extractor]] = {
        "mock": MockExtractor,
        "openai": OpenAIExtractor,
    }
    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(f"Unknown extraction provider: {provider}. Options: {list(_PROVIDERS)}")
    return cls(**kwargs)


__all__ = ["Extractor", "MockExtractor", "OpenAIExtractor", "extractor_for"]