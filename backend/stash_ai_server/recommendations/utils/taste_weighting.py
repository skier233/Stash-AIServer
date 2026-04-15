"""Taste-weight utilities: recency boost + data-depth scoring.

These utilities transform raw engagement scores into *taste weights* that
better reflect current user preferences.  Two main mechanisms:

1. **Recency boost** — An exponential time-decay multiplier that amplifies
   recent watches and attenuates older ones.  Half-life is configurable.

2. **Data-depth bonus** — A multiplier that rewards reference scenes with
   richer data (more tags, embeddings, AI tags) so the system picks better
   anchor points for taste profiles and centroids.

The resulting taste weights can be passed anywhere the system currently
accepts ``engagement_scores``.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

import sqlalchemy as sa

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.entity_embeddings import EntityEmbedding

_log = logging.getLogger(__name__)

# Recency defaults
DEFAULT_HALF_LIFE_DAYS = 30.0
RECENCY_FLOOR = 0.15          # minimum multiplier (never fully forget)
RECENCY_CEILING = 2.0         # maximum boost for very recent watches

# Data-depth scoring
DEPTH_TAG_WEIGHT = 0.20       # having Stash tags
DEPTH_EMBEDDING_WEIGHT = 0.40 # having visual embeddings
DEPTH_AI_TAG_WEIGHT = 0.30    # having AI tag durations
DEPTH_PERFORMER_WEIGHT = 0.10 # having performer data


# ---------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------

def _recency_multiplier(
    days_ago: float,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    floor: float = RECENCY_FLOOR,
    ceiling: float = RECENCY_CEILING,
) -> float:
    """Exponential decay with a floor.

    At ``days_ago == 0`` returns ``ceiling``.
    At ``days_ago == half_life`` returns ~``(ceiling - floor)/2 + floor``.
    Asymptotically approaches ``floor``.
    """
    if half_life_days <= 0:
        return 1.0
    decay = math.exp(-0.693 * days_ago / half_life_days)  # ln(2) ≈ 0.693
    return floor + (ceiling - floor) * decay


def apply_recency_boost(
    engagement_map: Mapping[int, float],
    watch_history: Sequence[Mapping[str, Any]],
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    floor: float = RECENCY_FLOOR,
    ceiling: float = RECENCY_CEILING,
) -> Dict[int, float]:
    """Multiply engagement scores by a recency factor.

    Parameters
    ----------
    engagement_map
        ``{scene_id: engagement_score}`` from the Noisy-OR scorer.
    watch_history
        Output of ``load_watch_history_summary()`` — each entry must have
        ``scene_id`` and ``last_seen`` (UTC datetime or None).
    half_life_days
        Time in days for the recency multiplier to halve.
    floor / ceiling
        Bounds for the multiplier.

    Returns a new ``{scene_id: boosted_score}`` dict.
    """
    if not engagement_map:
        return {}
    if half_life_days <= 0:
        return dict(engagement_map)

    now = datetime.now(timezone.utc)
    last_seen_map: Dict[int, datetime] = {}
    for entry in watch_history:
        sid = entry.get("scene_id")
        ls = entry.get("last_seen")
        if sid is not None and ls is not None:
            if isinstance(ls, datetime):
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                last_seen_map[int(sid)] = ls

    result: Dict[int, float] = {}
    for scene_id, eng_score in engagement_map.items():
        ls = last_seen_map.get(scene_id)
        if ls is not None:
            days = max(0.0, (now - ls).total_seconds() / 86400.0)
            mult = _recency_multiplier(days, half_life_days, floor, ceiling)
        else:
            mult = 1.0  # no timestamp → neutral
        result[scene_id] = eng_score * mult

    return result


# ---------------------------------------------------------------------------
# Data-depth scoring
# ---------------------------------------------------------------------------

def _count_scene_embeddings(scene_ids: Sequence[int]) -> Dict[int, int]:
    """Return ``{scene_id: embedding_count}`` for visual embeddings only."""
    if not scene_ids:
        return {}
    with get_session_local()() as session:
        rows = session.execute(
            sa.select(
                EntityEmbedding.entity_id,
                sa.func.count().label("cnt"),
            )
            .where(
                EntityEmbedding.entity_type == "scene",
                EntityEmbedding.entity_id.in_(list(scene_ids)),
                EntityEmbedding.embedding_type.like("visual_%"),
            )
            .group_by(EntityEmbedding.entity_id)
        ).all()
    return {int(r.entity_id): int(r.cnt) for r in rows}


def _count_scene_ai_tags(scene_ids: Sequence[int]) -> Set[int]:
    """Return the set of scene_ids that have AI tag timespan data."""
    if not scene_ids:
        return set()
    try:
        from stash_ai_server.models.ai_results import AIModelRun, AIResultTimespan
    except ImportError:
        return set()

    with get_session_local()() as session:
        rows = session.execute(
            sa.select(AIModelRun.entity_id)
            .join(AIResultTimespan, AIResultTimespan.run_id == AIModelRun.id)
            .where(
                AIModelRun.entity_type == "scene",
                AIModelRun.entity_id.in_(list(scene_ids)),
            )
            .group_by(AIModelRun.entity_id)
        ).all()
    return {int(r.entity_id) for r in rows}


def compute_data_depth(
    scene_ids: Sequence[int],
    *,
    corpus_tags: Mapping[int, Set[int]] | None = None,
    performer_map: Mapping[int, Set[int]] | None = None,
) -> Dict[int, float]:
    """Score each scene by data completeness in [0, 1].

    Components (weighted):
    - Has Stash tags (>= 2 tags for meaningful TF-IDF)
    - Has visual embeddings
    - Has AI tag durations
    - Has performer data

    Returns ``{scene_id: depth_score}``.
    """
    if not scene_ids:
        return {}

    id_list = list(scene_ids)

    # Stash tags
    has_tags: Set[int] = set()
    if corpus_tags:
        has_tags = {sid for sid in id_list if len(corpus_tags.get(sid, set())) >= 2}

    # Visual embeddings
    embed_counts = _count_scene_embeddings(id_list)

    # AI tags
    has_ai_tags = _count_scene_ai_tags(id_list)

    # Performers
    has_performers: Set[int] = set()
    if performer_map:
        has_performers = {sid for sid in id_list if performer_map.get(sid)}

    result: Dict[int, float] = {}
    for sid in id_list:
        score = 0.0
        if sid in has_tags:
            score += DEPTH_TAG_WEIGHT
        if embed_counts.get(sid, 0) > 0:
            score += DEPTH_EMBEDDING_WEIGHT
        if sid in has_ai_tags:
            score += DEPTH_AI_TAG_WEIGHT
        if sid in has_performers:
            score += DEPTH_PERFORMER_WEIGHT
        result[sid] = score

    return result


def build_taste_weights(
    engagement_map: Mapping[int, float],
    watch_history: Sequence[Mapping[str, Any]],
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    data_depth: Mapping[int, float] | None = None,
    depth_influence: float = 0.3,
) -> Dict[int, float]:
    """Combine engagement, recency, and data depth into final taste weights.

    ``depth_influence`` controls how much data depth affects the weight:
    - 0.0 = data depth ignored (pure engagement × recency)
    - 1.0 = data depth has equal influence to the score

    Formula: ``weight = recency_boosted_engagement × (1 + depth_influence × depth)``
    """
    recency_boosted = apply_recency_boost(
        engagement_map, watch_history, half_life_days=half_life_days,
    )

    if not data_depth or depth_influence <= 0:
        return recency_boosted

    result: Dict[int, float] = {}
    for sid, score in recency_boosted.items():
        depth = data_depth.get(sid, 0.0)
        result[sid] = score * (1.0 + depth_influence * depth)
    return result
