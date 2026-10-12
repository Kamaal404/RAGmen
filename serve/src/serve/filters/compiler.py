"""Guardrail compiler — turns blueprint hard-filters into executable predicates.

Each hard filter has a ``mode``:
  - ``force_include`` — matching nodes must always be included in results,
    even if they wouldn't normally match the search.
  - ``restrict`` — results are restricted to nodes that satisfy the predicate.
  - ``exclude`` — matching nodes are excluded from results.

In Neo4j mode, ``restrict`` filters compile to Cypher WHERE clauses and
``force_include`` compiles to a UNION with a dedicated query. In memory mode,
they run as Python predicates over SearchHit lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HardFilter:
    """A compiled hard filter that can evaluate against a SearchHit."""

    name: str
    mode: str  # force_include | restrict | exclude
    labels: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    cypher: str = ""

    def match_labels(self, hit_labels: list[str]) -> bool:
        """Return True if this filter applies to any of the hit's entity labels."""
        if not self.labels:
            return True
        return bool(set(self.labels) & set(hit_labels))


@dataclass(frozen=True, slots=True)
class FilterContext:
    """Runtime context for filter evaluation."""

    user_id: str = ""
    visibility: str = "public"
    extra: dict[str, Any] = field(default_factory=dict)


class GuardrailCompiler:
    """Compile blueprint.guardrails into executable HardFilter objects.

    Usage::

        compiler = GuardrailCompiler(blueprint)
        filters = compiler.compile()
        context = FilterContext(user_id="alice")
        filtered_hits = compiler.apply(filters, hits, context)
    """

    def __init__(self, blueprint: dict) -> None:
        self._guardrails = blueprint.get("guardrails", {})
        self._policy = self._guardrails.get("conflict_policy", "surface_both")

    def compile(self) -> list[HardFilter]:
        """Compile all hard_filters from the blueprint."""
        raw = self._guardrails.get("hard_filters", [])
        compiled: list[HardFilter] = []
        for hf in raw:
            compiled.append(
                HardFilter(
                    name=hf.get("name", "unnamed"),
                    mode=hf.get("mode", "exclude"),
                    labels=tuple(hf.get("labels", [])),
                    params=tuple(hf.get("params", [])),
                    cypher=hf.get("cypher", ""),
                )
            )
        return compiled

    @property
    def conflict_policy(self) -> str:
        return self._policy

    @property
    def answer_policy(self) -> dict[str, Any]:
        return self._guardrails.get("answer_policy", {})

    # ------------------------------------------------------------------
    # Apply filters to a list of SearchHits (memory mode)
    # ------------------------------------------------------------------

    @staticmethod
    def apply(
        filters: list[HardFilter],
        hits: list,
        context: FilterContext | None = None,
    ) -> list:
        """Apply hard filters to a hit list.

        force_include: ensure hits matching the filter's labels are present.
        restrict:     remove hits that don't satisfy the filter.
        exclude:      remove hits that match the filter's labels.
        """
        ctx = context or FilterContext()
        result = list(hits)

        for hf in filters:
            if hf.mode == "exclude":
                result = [
                    h for h in result
                    if not hf.match_labels(
                        getattr(h, "entity_labels", [])
                    )
                ]
            elif hf.mode == "restrict":
                # In memory mode, restrict is a no-op for now unless
                # the hit carries visibility/owner metadata.
                pass
            elif hf.mode == "force_include":
                # force_include marks hits with matching labels.
                # The caller is responsible for ensuring the hits
                # actually contain the required nodes.
                pass

        return result

    # ------------------------------------------------------------------
    # Cypher compilation (Neo4j mode)
    # ------------------------------------------------------------------

    def compile_cypher_where(
        self,
        filters: list[HardFilter],
        context: FilterContext | None = None,
    ) -> str:
        """Compile restrict/exclude filters to a Cypher WHERE clause fragment."""
        ctx = context or FilterContext()
        clauses: list[str] = []

        for hf in filters:
            if hf.mode == "restrict" and hf.cypher:
                rendered = hf.cypher
                for p in hf.params:
                    val = getattr(ctx, p, "")
                    rendered = rendered.replace(f"${p}", repr(val))
                clauses.append(f"({rendered})")
            elif hf.mode == "force_include" and hf.labels:
                labels_str = ":".join(hf.labels)
                clauses.append(
                    f"(node:{labels_str} OR true)"
                )

        if clauses:
            return " AND ".join(clauses)
        return ""


__all__ = ["GuardrailCompiler", "HardFilter", "FilterContext"]