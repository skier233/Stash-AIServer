"""Main engagement scorer.

Combines all available signals into a single engagement score per scene
using a **Noisy-OR** (cumulative evidence) model:

    score = 1 - Π(1 - v_i × r_i × w_i)  for all available signals

Each signal is a piece of independent evidence that the user liked the
scene.  Adding *any* positive signal can only increase the score — more
data always helps, never hurts.  Signals with high reliability (explicit
rating) dominate because they consume the most "headroom", while
low-reliability signals (play_count) contribute small boosts.

Confidence reflects overall signal coverage (how much data we had).
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from .data_loader import load_engagement_data, RawSignalBundle
from .signals import SIGNALS, SIGNAL_MAP, SignalDefinition
from .types import EngagementResult, SignalValue

_log = logging.getLogger(__name__)


def _map_signal_to_raw(signal: SignalDefinition, bundle: RawSignalBundle) -> Any:
    """Map a signal definition to the raw value from the data bundle.

    Some signals need merging logic (handled in data_loader), others map
    directly to a single key.
    """
    name = signal.name
    mapping: dict[str, str] = {
        "explicit_rating": "explicit_rating",
        "stash_o_counter": "stash_o_counter",
        "derived_o_count": "ai_derived_o_count",
        "watch_completeness": "ai_best_watch_percent",
        "watch_duration_ratio": "merged_total_watched_s",
        "rewatch_sessions": "ai_session_count",
        "play_count": "merged_play_count",
    }
    key = mapping.get(name, name)
    return bundle.get(key)


def score_scene(
    scene_id: int,
    bundle: RawSignalBundle,
    *,
    weight_overrides: dict[str, float] | None = None,
    context_overrides: dict[str, Any] | None = None,
) -> EngagementResult:
    """Compute engagement score for a single scene from its raw data bundle.

    Parameters
    ----------
    scene_id : int
        The scene ID being scored.
    bundle : RawSignalBundle
        Merged data from all sources (output of data_loader).
    weight_overrides : dict, optional
        Per-signal weight overrides. Keys are signal names, values are floats.
        Signals not listed use default weight of 1.0.
    context_overrides : dict, optional
        Extra context values passed to signal normalizers.
    """
    ctx = dict(bundle)
    if context_overrides:
        ctx.update(context_overrides)

    weights = weight_overrides or {}
    signals_out: dict[str, SignalValue] = {}
    total_possible_weight = 0.0
    available_weight = 0.0
    available_count = 0

    # Collect contributions for the Noisy-OR model
    complement = 1.0  # running product of (1 - contribution_i)

    for signal_def in SIGNALS:
        w = weights.get(signal_def.name, 1.0)
        if w <= 0:
            continue

        effective_weight = w * signal_def.reliability
        total_possible_weight += effective_weight

        raw = _map_signal_to_raw(signal_def, bundle)
        normalized = signal_def.normalize(raw, ctx)

        if normalized is None:
            signals_out[signal_def.name] = SignalValue(
                name=signal_def.name,
                value=0.0,
                raw=raw,
                available=False,
                reliability=signal_def.reliability,
                source="n/a",
                weight=w,
            )
            continue

        available_count += 1
        available_weight += effective_weight

        # Noisy-OR contribution: value × reliability × weight, clamped to [0,1)
        contribution = min(0.999, max(0.0, normalized * signal_def.reliability * w))
        complement *= (1.0 - contribution)

        # Determine source
        source = "merged"
        if signal_def.name.startswith("stash_"):
            source = "stash_db"
        elif signal_def.name.startswith("derived_") or signal_def.name.startswith("watch_"):
            source = "ai_server"

        signals_out[signal_def.name] = SignalValue(
            name=signal_def.name,
            value=normalized,
            raw=raw,
            available=True,
            reliability=signal_def.reliability,
            source=source,
            weight=w,
        )

    # Noisy-OR: score = 1 - Π(1 - contribution_i)
    score = 1.0 - complement if available_count > 0 else 0.0
    confidence = available_weight / total_possible_weight if total_possible_weight > 0 else 0.0

    return EngagementResult(
        entity_id=scene_id,
        entity_type="scene",
        score=score,
        confidence=confidence,
        signal_count=available_count,
        total_possible=len([s for s in SIGNALS if weights.get(s.name, 1.0) > 0]),
        signals=signals_out,
    )


def score_scenes(
    scene_ids: Sequence[int],
    *,
    entity_type: str = "scene",
    weight_overrides: dict[str, float] | None = None,
    context_overrides: dict[str, Any] | None = None,
    scene_durations: dict[int, float] | None = None,
) -> dict[int, EngagementResult]:
    """Batch-score engagement for multiple scenes.

    Parameters
    ----------
    scene_ids : sequence of int
        Scene IDs to score.
    entity_type : str
        "scene" or "image".
    weight_overrides : dict, optional
        Per-signal weight overrides.
    context_overrides : dict, optional
        Extra context passed to signal normalizers.
    scene_durations : dict, optional
        External duration data {scene_id: seconds}. Merged into bundles
        when Stash DB duration is missing.
    """
    if not scene_ids:
        return {}

    bundles = load_engagement_data(scene_ids, entity_type=entity_type)

    # Inject externally-provided durations where missing
    if scene_durations:
        for sid, dur in scene_durations.items():
            if sid in bundles:
                if not bundles[sid].get("scene_duration"):
                    bundles[sid]["scene_duration"] = dur
            else:
                bundles[sid] = {"scene_duration": dur}

    results: dict[int, EngagementResult] = {}
    for sid in scene_ids:
        sid_int = int(sid)
        bundle = bundles.get(sid_int, {})
        results[sid_int] = score_scene(
            sid_int,
            bundle,
            weight_overrides=weight_overrides,
            context_overrides=context_overrides,
        )

    return results


def score_all_watched_scenes(
    *,
    entity_type: str = "scene",
    weight_overrides: dict[str, float] | None = None,
    context_overrides: dict[str, Any] | None = None,
    limit: int | None = None,
    min_score: float = 0.0,
    min_confidence: float = 0.0,
    include_rated: bool = True,
) -> list[EngagementResult]:
    """Score all scenes that have *any* engagement data.

    When include_rated=True (default), also includes scenes that have
    explicit ratings (AI custom or Stash native) even if they have no
    watch history.  This ensures rated-but-unwatched scenes appear in
    taste profiles and recommendation reference sets.

    Returns results sorted by score descending, optionally filtered.
    Useful for the debug endpoint and for recommendation plugins that
    want "the user's top engaged scenes."
    """
    from .data_loader import _load_ai_server_data, _load_stash_data

    # First, discover all scene IDs with any engagement data
    ai_data = _load_ai_server_data([], entity_type=entity_type)
    # AI server loader with empty list returns empty — need a different approach
    # Load all SceneDerived IDs + all SceneWatch scene IDs
    all_ids: set[int] = set()
    try:
        from stash_ai_server.db.session import get_session_local
        import sqlalchemy as sa
        from stash_ai_server.models.interaction import SceneDerived, SceneWatch

        with get_session_local()() as session:
            for (sid,) in session.execute(sa.select(SceneDerived.scene_id)):
                all_ids.add(int(sid))
            for (sid,) in session.execute(
                sa.select(SceneWatch.scene_id).distinct()
            ):
                all_ids.add(int(sid))
    except Exception:
        _log.exception("Failed to enumerate watched scene IDs")

    # Also discover scenes with explicit ratings (rated-but-unwatched)
    if include_rated:
        try:
            from stash_ai_server.db.session import get_session_local
            import sqlalchemy as sa
            from stash_ai_server.models.ratings import EntityRating

            with get_session_local()() as session:
                for (eid,) in session.execute(
                    sa.select(EntityRating.entity_id).where(
                        EntityRating.entity_type == "scene",
                        EntityRating.rating_key == "default",
                    )
                ):
                    try:
                        all_ids.add(int(eid))
                    except (TypeError, ValueError):
                        pass
        except Exception:
            _log.debug("Failed to enumerate AI-rated scene IDs", exc_info=True)

        # Stash native ratings (scenes.rating IS NOT NULL and != 0)
        try:
            from stash_ai_server.utils.stash_db import get_stash_session_factory
            import sqlalchemy as sa

            factory = get_stash_session_factory()
            if factory:
                with factory() as session:
                    scenes_table = sa.Table(
                        "scenes", sa.MetaData(), autoload_with=session.bind,
                    )
                    rating_col = None
                    for col in scenes_table.columns:
                        if col.name == "rating":
                            rating_col = col
                            break
                    if rating_col is not None:
                        for (sid,) in session.execute(
                            sa.select(scenes_table.c.id).where(
                                rating_col.isnot(None), rating_col != 0,
                            )
                        ):
                            all_ids.add(int(sid))
        except Exception:
            _log.debug("Failed to enumerate Stash-rated scene IDs", exc_info=True)

    if not all_ids:
        return []

    results = score_scenes(
        list(all_ids),
        entity_type=entity_type,
        weight_overrides=weight_overrides,
        context_overrides=context_overrides,
    )

    scored = sorted(results.values(), key=lambda r: r.score, reverse=True)

    if min_score > 0:
        scored = [r for r in scored if r.score >= min_score]
    if min_confidence > 0:
        scored = [r for r in scored if r.confidence >= min_confidence]
    if limit and limit > 0:
        scored = scored[:limit]

    return scored
