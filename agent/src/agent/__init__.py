"""Lattice Agent — answer policy layer.

Sits on top of Serve's retrievers and formats answers according to the
blueprint's declared answer policy. Domain-agnostic: same code works for
fire books, legal corpora, or recipes.

Answer policy (from blueprint.guardrails.answer_policy):
  - require_citation: every claim must name its source chunk
  - refuse_outside_corpus: say "not in the corpus" instead of guessing
  - conflict_policy: surface both sides with attribution by default
"""

from .answers import Agent, AnswerPolicy, CitedAnswer, format_answer
from .serve_client import DirectClient

__all__ = ["Agent", "AnswerPolicy", "CitedAnswer", "DirectClient", "format_answer"]