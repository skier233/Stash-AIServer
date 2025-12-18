from __future__ import annotations
"""Version compatibility helpers shared across the backend."""

from packaging import version as _v
import os
from typing import Optional

DEFAULT_FRONTEND_MIN_VERSION = ">=0.8.0"
FRONTEND_MIN_VERSION = os.getenv("AI_SERVER_FRONTEND_MIN_VERSION", DEFAULT_FRONTEND_MIN_VERSION)

_DEV_TOKENS = ("dev", "local", "snapshot", "dirty")


def is_dev_version(value: Optional[str]) -> bool:
    """Return True if the provided version string represents a dev/local build."""
    if not value:
        return False
    lowered = value.strip().lower()
    if not lowered:
        return False
    if lowered.startswith("0.0.0"):
        return True
    return any(token in lowered for token in _DEV_TOKENS)


def version_satisfies(actual: Optional[str], requirement: Optional[str]) -> bool:
    """Evaluate whether *actual* satisfies the semver-like *requirement* expression."""
    if not requirement:
        return True
    if not actual:
        return False
    if is_dev_version(actual):  # dev builds bypass compatibility gates
        return True

    try:
        current = _v.parse(actual)
    except Exception:
        return False

    expr = " ".join(requirement.replace(",", " ").split())
    if not expr:
        return True

    clauses = [clause.strip() for clause in expr.split(" ") if clause.strip()]
    if not clauses:
        return True

    for clause in clauses:
        operator = None
        target = clause
        for candidate in (">=", "<=", ">", "<", "==", "="):
            if clause.startswith(candidate):
                operator = candidate
                target = clause[len(candidate):].strip()
                break
        if operator is None:
            operator = "=="
            target = clause
        if not target:
            continue
        try:
            target_version = _v.parse(target)
        except Exception:
            return False

        if operator in ("==", "="):
            if current != target_version:
                return False
        elif operator == ">=":
            if current < target_version:
                return False
        elif operator == ">":
            if current <= target_version:
                return False
        elif operator == "<=":
            if current > target_version:
                return False
        elif operator == "<":
            if current >= target_version:
                return False
        else:
            return False

    return True
