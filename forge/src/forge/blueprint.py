# ruff: noqa: E501
"""Blueprint loader: validate, hash, and parse a blueprint.yaml for Forge.

Reuses the validation logic from `blueprint/scripts/validate_blueprint.py`
so Forge and Blueprint stay in sync on what makes a blueprint valid.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


class BlueprintError(Exception):
    """The blueprint failed validation. .errors and .warnings carry the detail."""

    def __init__(self, message: str, errors: list[str], warnings: list[str]) -> None:
        self.errors = errors
        self.warnings = warnings
        super().__init__(message)


class BlueprintNotFoundError(BlueprintError):
    """The blueprint file does not exist."""


# ---------------------------------------------------------------------------
# Import the shared validator once
# ---------------------------------------------------------------------------

_VALIDATOR = None


def _load_validator() -> Any:
    """Lazy-import validate_blueprint.py, which is outside the forge package."""
    global _VALIDATOR
    if _VALIDATOR is not None:
        return _VALIDATOR

    path = Path(__file__).resolve().parent.parent.parent.parent
    validator_py = path / "blueprint" / "scripts" / "validate_blueprint.py"
    if not validator_py.exists():
        msg = f"validator not found at {validator_py}"
        raise BlueprintError(msg, [msg], [])

    spec = importlib.util.spec_from_file_location("validate_blueprint", validator_py)
    if spec is None or spec.loader is None:
        msg = f"could not load validator from {validator_py}"
        raise BlueprintError(msg, [msg], [])

    mod = importlib.util.module_from_spec(spec)
    # Expose the SPEC path so the validator can find the schema
    sys.modules["validate_blueprint"] = mod
    spec.loader.exec_module(mod)
    _VALIDATOR = mod
    return _VALIDATOR


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_hash(bp: dict) -> str:
    """Return the deterministic content hash of a blueprint dict.

    Uses the same canonical-JSON approach as validate_blueprint.py so the
    hashes computed at design time and build time are identical.
    """
    body = {k: v for k, v in bp.items() if k != "blueprint_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_blueprint(bp: dict, strict: bool = False) -> tuple[bool, list[str], list[str]]:
    """Check a blueprint dict against both the JSON Schema and semantic rules.

    Returns (is_valid, errors, warnings). When *strict* is True, warnings
    are treated as errors (is_valid becomes False if any warning exists).
    """
    mod = _load_validator()
    # One entry point, so a check added to the validator is enforced here
    # without a second edit. Listing the checks by hand let them drift apart.
    errors, warnings = mod.run_checks(bp)
    is_valid = len(errors) == 0
    if strict:
        is_valid = is_valid and len(warnings) == 0
    return is_valid, errors, warnings


def load_blueprint(path: str | Path, strict: bool = False) -> dict:
    """Load, validate and return a blueprint.yaml.

    Raises:
        BlueprintNotFoundError — file does not exist.
        BlueprintError — validation failed (errors + warnings collected).
    """
    import yaml

    path_obj = Path(path)
    if not path_obj.exists():
        msg = f"blueprint not found: {path_obj}"
        raise BlueprintNotFoundError(msg, [msg], [])

    raw = path_obj.read_text(encoding="utf-8")
    try:
        bp = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        msg = f"invalid YAML: {e}"
        raise BlueprintError(msg, [msg], []) from e

    if not isinstance(bp, dict):
        msg = "blueprint must be a YAML mapping"
        raise BlueprintError(msg, [msg], [])

    valid, errors, warnings = validate_blueprint(bp, strict=strict)
    if not valid:
        raise BlueprintError(
            f"blueprint validation failed ({len(errors)} error(s), {len(warnings)} warning(s))",
            errors,
            warnings,
        )

    # Stamp the hash so downstream code can trust it
    bp["blueprint_hash"] = compute_hash(bp)
    return bp