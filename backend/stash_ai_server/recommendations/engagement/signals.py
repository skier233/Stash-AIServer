"""Signal definitions for the engagement scoring subsystem.

Each signal is a function that takes raw data and returns a normalized
0.0–1.0 value.  Signals also carry a *reliability* rating that reflects
how strongly the signal type corresponds to genuine user engagement.

Signals are split into two tiers:
  EXPLICIT (user intent) — form the base score, never reduced by behavioral:
    1.0  explicit_rating     — user explicitly rated the scene (z-score vs user mean)
    0.95 stash_o_counter     — user explicitly marked orgasm
    0.85 derived_o_count     — session-duration-qualified o-count
  BEHAVIORAL (indirect evidence) — supplement explicit; primary when no explicit:
    0.70 watch_completeness  — best-session watch % z-scored vs user's own patterns
    0.65 watch_duration      — total watch time vs scene duration (merged sources)
    0.50 rewatch_sessions    — came back to this scene multiple times
    0.20 play_count          — raw play count (low: could be just browsing)

Removed signals (by design):
  - stash_play_duration_ratio: double-counts watch_duration_ratio (same data source)
  - recency: doesn't indicate preference, only when viewed; belongs in reco layer
  - resume_position: too noisy, doesn't reliably indicate enjoyment
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SignalDefinition:
    """Metadata for a single engagement signal."""

    name: str
    reliability: float  # 0.0–1.0 inherent confidence in this signal type
    normalize: Callable[[Any, dict[str, Any]], float | None]
    tier: str = "behavioral"  # "explicit" (user intent) or "behavioral" (indirect)
    description: str = ""


def _log_scale(value: float, cap: float) -> float:
    """Logarithmic normalization: log(1+n) / log(1+cap), clamped to [0,1]."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(cap))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Individual signal normalizers
# ---------------------------------------------------------------------------
# Each normalizer receives (raw_value, context_dict) and returns 0.0–1.0
# or None if the data is not available.

def _norm_explicit_rating(raw: Any, ctx: dict) -> float | None:
    """Z-score normalize rating against user's own rating distribution.

    Adapts to different rating styles (5-star whole increments, decimal
    sliders, generous vs stingy raters) by comparing each rating to the
    user's mean and standard deviation.  Result mapped through sigmoid
    so z=0 → 0.5, z=+2 → ~0.88, z=-2 → ~0.12.

    Falls back to raw/100 when stats are unavailable (single rating).
    """
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    mean = ctx.get("rating_mean")
    std = ctx.get("rating_std")
    if mean is not None and std is not None and std > 0:
        z = (v - mean) / std
        # Sigmoid maps z-score to 0–1 smoothly
        normalized = 1.0 / (1.0 + math.exp(-z))
    else:
        # Fallback: no distribution data (only 1 rating or no stats)
        normalized = v / 100.0
    return _clamp01(normalized)


def _norm_stash_o_counter(raw: Any, ctx: dict) -> float | None:
    """Stash scenes.o_counter — explicit orgasm count, log-scaled."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None  # zero is indistinguishable from "never set"
    return _log_scale(v, cap=20.0)


def _norm_derived_o_count(raw: Any, ctx: dict) -> float | None:
    """AI Server derived_o_count (session-qualified), log-scaled."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return _log_scale(v, cap=20.0)


def _norm_watch_completeness(raw: Any, ctx: dict) -> float | None:
    """Best single-session watch_percent, z-score normalized against user's
    own viewing patterns (only counting sessions with >15s of watch time).

    Adapts to the user: someone who always watches 90% will need higher
    completeness for this to register as "above average", while someone
    who typically watches 30% gets credit for a 50% completion.

    Falls back to direct 0–1 mapping when stats are unavailable.
    """
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    # Normalize to 0–1 scale
    if v > 1.0:
        v = v / 100.0

    mean = ctx.get("watch_completeness_mean")
    std = ctx.get("watch_completeness_std")
    if mean is not None and std is not None and std > 0:
        m = float(mean)
        s = float(std)
        # Ensure same scale
        if m > 1.0:
            m = m / 100.0
            s = s / 100.0
        z = (v - m) / s
        return _clamp01(1.0 / (1.0 + math.exp(-z)))
    return _clamp01(v)


def _norm_watch_duration_ratio(raw: Any, ctx: dict) -> float | None:
    """Total watched seconds / scene duration.  Capped at 3× (heavy rewatch)."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    duration = ctx.get("scene_duration")
    if not duration or duration <= 0:
        return None
    ratio = v / float(duration)
    if ratio <= 0:
        return None
    return _clamp01(ratio / 3.0)  # 3× full watch = 1.0


def _norm_rewatch_sessions(raw: Any, ctx: dict) -> float | None:
    """Number of distinct watch sessions, log-scaled."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return _log_scale(v, cap=30.0)


def _norm_play_count(raw: Any, ctx: dict) -> float | None:
    """Raw play count (stash or AI server).  Low reliability because
    users frequently open scenes briefly to organize/tag them."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return _log_scale(v, cap=50.0)


# _norm_recency, _norm_resume_position, _norm_stash_play_duration removed:
#   recency — doesn't indicate preference, belongs in recommendation layer
#   resume_position — too noisy, doesn't reliably indicate enjoyment
#   stash_play_duration — double-counts watch_duration_ratio (merged sources)


# ---------------------------------------------------------------------------
# Signal registry
# ---------------------------------------------------------------------------

SIGNALS: list[SignalDefinition] = [
    # --- Explicit tier: direct user intent ---
    SignalDefinition(
        name="explicit_rating",
        reliability=1.0,
        normalize=_norm_explicit_rating,
        tier="explicit",
        description="User-provided rating z-score normalized against their own distribution",
    ),
    SignalDefinition(
        name="stash_o_counter",
        reliability=0.95,
        normalize=_norm_stash_o_counter,
        tier="explicit",
        description="Stash o_counter (user-triggered orgasm count)",
    ),
    SignalDefinition(
        name="derived_o_count",
        reliability=0.85,
        normalize=_norm_derived_o_count,
        tier="explicit",
        description="AI Server session-qualified o_count",
    ),
    # --- Behavioral tier: indirect evidence ---
    SignalDefinition(
        name="watch_completeness",
        reliability=0.70,
        normalize=_norm_watch_completeness,
        tier="behavioral",
        description="Best single-session watch %, z-scored vs user viewing patterns",
    ),
    SignalDefinition(
        name="watch_duration_ratio",
        reliability=0.65,
        normalize=_norm_watch_duration_ratio,
        tier="behavioral",
        description="Total watched seconds / scene duration (merged stash + AI sources)",
    ),
    SignalDefinition(
        name="rewatch_sessions",
        reliability=0.50,
        normalize=_norm_rewatch_sessions,
        tier="behavioral",
        description="Number of distinct viewing sessions",
    ),
    SignalDefinition(
        name="play_count",
        reliability=0.20,
        normalize=_norm_play_count,
        tier="behavioral",
        description="Raw play count (low reliability — could be browsing)",
    ),
]

SIGNAL_MAP: dict[str, SignalDefinition] = {s.name: s for s in SIGNALS}
