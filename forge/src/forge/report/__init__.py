"""Drift report — Forge's feedback loop to Blueprint.

Forge sees things Blueprint never will: the long tail of real sources.
The drift report measures what extraction found that didn't fit the
blueprint, so the ontology designer can see when the schema is going stale.
"""

from .drift import DriftReport, build_drift_report, print_report

__all__ = ["DriftReport", "build_drift_report", "print_report"]