"""Negative-signal utilities for the recommendation system.

Learns what the user does NOT like by analysing low-engagement watched
scenes.  Builds "anti-profiles" (negative tag vectors, negative performer
affinity, negative embedding centroids) that are used to penalise
candidates matching patterns the user historically disliked.

Design decisions:
  - Only considers scenes the user actually watched (≥ min_watch_seconds)
    but did not enjoy (engagement below a threshold).
  - The negative threshold is configurable — defaults to the 25th percentile
    of engagement scores among watched scenes.
  - Negative signals are subtracted from composite scores, NOT used as hard
    filters.  A small negative penalty nudges results rather than blocking them.

Gracefully returns empty structures when there is insufficient data.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

import numpy as np

_log = logging.getLogger(__name__)

# Defaults
DEFAULT_NEGATIVE_PERCENTILE = 25  # scenes below this engagement percentile → "disliked"
MIN_NEGATIVE_SCENES = 5           # need at least this many disliked scenes to form a signal


# ---------------------------------------------------------------------------
# Threshold computation
# ---------------------------------------------------------------------------

def _compute_dislike_threshold(
    engagement_map: Mapping[int, float],
    percentile: int = DEFAULT_NEGATIVE_PERCENTILE,
) -> float:
    """Compute the engagement score below which scenes are considered disliked.

    Uses the Nth percentile of all engagement scores.
    """
    if not engagement_map:
        return 0.0
    scores = sorted(engagement_map.values())
    idx = max(0, int(len(scores) * percentile / 100.0) - 1)
    return scores[idx]


def split_by_engagement(
    watched_scene_ids: Sequence[int],
    engagement_map: Mapping[int, float],
    *,
    percentile: int = DEFAULT_NEGATIVE_PERCENTILE,
    min_negative: int = MIN_NEGATIVE_SCENES,
) -> Tuple[Set[int], Set[int]]:
    """Split watched scenes into liked and disliked sets.

    Returns ``(liked_ids, disliked_ids)``.  If there aren't enough disliked
    scenes, returns ``(all_ids, empty_set)``.
    """
    if not watched_scene_ids or not engagement_map:
        return set(watched_scene_ids), set()

    threshold = _compute_dislike_threshold(engagement_map, percentile)
    liked: Set[int] = set()
    disliked: Set[int] = set()

    for sid in watched_scene_ids:
        eng = engagement_map.get(sid)
        if eng is None:
            liked.add(sid)  # no data → assume neutral
        elif eng <= threshold:
            disliked.add(sid)
        else:
            liked.add(sid)

    if len(disliked) < min_negative:
        _log.debug(
            "negative signals: only %d disliked scenes (need %d), skipping",
            len(disliked), min_negative,
        )
        return set(watched_scene_ids), set()

    _log.info(
        "negative signals: %d liked, %d disliked (threshold=%.3f at p%d)",
        len(liked), len(disliked), threshold, percentile,
    )
    return liked, disliked


# ---------------------------------------------------------------------------
# Negative tag profile
# ---------------------------------------------------------------------------

def build_negative_tag_profile(
    *,
    disliked_scene_tags: Mapping[int, Set[int]],
    liked_scene_tags: Mapping[int, Set[int]],
    idf: Mapping[int, float],
) -> Dict[int, float]:
    """Build a "dislike" tag vector from low-engagement scenes.

    Tags are only included in the negative profile if they appear *more*
    in disliked scenes than in liked scenes (relative to set sizes).  This
    prevents penalising universally common tags.

    Returns an L2-normalised vector ``{tag_id: weight}``.
    """
    if not disliked_scene_tags:
        return {}

    n_disliked = max(len(disliked_scene_tags), 1)
    n_liked = max(len(liked_scene_tags), 1)

    # Frequency of each tag in disliked vs liked scenes
    disliked_freq: Dict[int, int] = defaultdict(int)
    liked_freq: Dict[int, int] = defaultdict(int)

    for tags in disliked_scene_tags.values():
        for tid in tags:
            disliked_freq[tid] += 1
    for tags in liked_scene_tags.values():
        for tid in tags:
            liked_freq[tid] += 1

    # Only keep tags that are disproportionately in disliked scenes
    raw: Dict[int, float] = {}
    for tid, d_count in disliked_freq.items():
        d_rate = d_count / n_disliked
        l_rate = liked_freq.get(tid, 0) / n_liked
        # Tag must appear at higher rate in disliked scenes
        if d_rate > l_rate * 1.25:  # 25% higher frequency threshold
            excess = d_rate - l_rate
            raw[tid] = excess * idf.get(tid, 1.0)

    if not raw:
        return {}

    magnitude = math.sqrt(sum(v * v for v in raw.values()))
    if magnitude <= 0:
        return {}
    return {tid: v / magnitude for tid, v in raw.items()}


def build_negative_performer_profile(
    *,
    disliked_scene_performers: Mapping[int, Set[int]],
    liked_scene_performers: Mapping[int, Set[int]],
) -> Dict[int, float]:
    """Build negative performer affinity from disliked scenes.

    Returns ``{performer_id: penalty}`` in [0, 1] for performers that appear
    disproportionately in disliked scenes.
    """
    if not disliked_scene_performers:
        return {}

    n_disliked = max(len(disliked_scene_performers), 1)
    n_liked = max(len(liked_scene_performers), 1)

    disliked_freq: Dict[int, int] = defaultdict(int)
    liked_freq: Dict[int, int] = defaultdict(int)

    for performers in disliked_scene_performers.values():
        for pid in performers:
            disliked_freq[pid] += 1
    for performers in liked_scene_performers.values():
        for pid in performers:
            liked_freq[pid] += 1

    penalties: Dict[int, float] = {}
    for pid, d_count in disliked_freq.items():
        d_rate = d_count / n_disliked
        l_rate = liked_freq.get(pid, 0) / n_liked
        if d_rate > l_rate * 1.5:  # 50% higher threshold for performers
            penalties[pid] = min(1.0, d_rate - l_rate)

    if penalties:
        max_p = max(penalties.values())
        if max_p > 0:
            penalties = {pid: v / max_p for pid, v in penalties.items()}

    return penalties


# ---------------------------------------------------------------------------
# Negative scoring helpers
# ---------------------------------------------------------------------------

def compute_negative_tag_penalty(
    scene_vector: Mapping[int, float],
    negative_profile: Mapping[int, float],
) -> float:
    """Cosine similarity between a candidate scene and the negative profile.

    Returns a value in [0, 1] representing how much this scene matches
    disliked patterns.  Higher = more similar to disliked content.
    """
    if not scene_vector or not negative_profile:
        return 0.0
    # Dot product of L2-normalized vectors
    if len(scene_vector) > len(negative_profile):
        small, big = negative_profile, scene_vector
    else:
        small, big = scene_vector, negative_profile
    return max(0.0, sum(v * big[k] for k, v in small.items() if k in big))


def compute_negative_performer_penalty(
    scene_performers: Set[int] | None,
    negative_performer_profile: Mapping[int, float],
) -> float:
    """Compute performer penalty for a candidate scene.

    Returns the max penalty among the scene's performers.
    """
    if not scene_performers or not negative_performer_profile:
        return 0.0
    return max(
        (negative_performer_profile.get(pid, 0.0) for pid in scene_performers),
        default=0.0,
    )


def compute_combined_negative_penalty(
    *,
    scene_vector: Mapping[int, float],
    negative_tag_profile: Mapping[int, float],
    scene_performers: Set[int] | None = None,
    negative_performer_profile: Mapping[int, float] | None = None,
    tag_penalty_weight: float = 0.7,
    performer_penalty_weight: float = 0.3,
) -> float:
    """Combine tag and performer negative penalties into a single [0,1] value.

    The combined penalty is a weighted average of tag and performer penalties,
    then scaled by the weights.
    """
    tag_pen = compute_negative_tag_penalty(scene_vector, negative_tag_profile)
    perf_pen = 0.0
    if negative_performer_profile:
        perf_pen = compute_negative_performer_penalty(
            scene_performers, negative_performer_profile,
        )

    total_w = tag_penalty_weight + performer_penalty_weight
    if total_w <= 0:
        return 0.0
    return (tag_penalty_weight * tag_pen + performer_penalty_weight * perf_pen) / total_w


def compute_negative_detail(
    *,
    scene_vector: Mapping[int, float],
    negative_tag_profile: Mapping[int, float],
    scene_performers: Set[int] | None = None,
    negative_performer_profile: Mapping[int, float] | None = None,
) -> Dict[str, Any]:
    """Return a detailed breakdown of the negative penalty for debug UIs.

    Returns dict with:
    - ``tag_contributions``: top negative-matching tags with per-tag penalty
    - ``performer_penalties``: performers that triggered negative penalty
    - ``tag_penalty``: aggregate negative tag cosine sim
    - ``performer_penalty``: aggregate performer penalty
    """
    tag_contribs: List[Dict[str, Any]] = []
    tag_pen = 0.0
    if scene_vector and negative_tag_profile:
        for tid in scene_vector:
            if tid in negative_tag_profile:
                c = scene_vector[tid] * negative_tag_profile[tid]
                if c > 0:
                    tag_contribs.append({"tag_id": tid, "penalty": round(c, 6)})
                    tag_pen += c
        tag_contribs.sort(key=lambda x: x["penalty"], reverse=True)

    perf_pens: List[Dict[str, Any]] = []
    perf_pen = 0.0
    if scene_performers and negative_performer_profile:
        for pid in scene_performers:
            p = negative_performer_profile.get(pid, 0.0)
            if p > 0:
                perf_pens.append({"performer_id": pid, "penalty": round(p, 4)})
                perf_pen = max(perf_pen, p)

    return {
        "tag_contributions": tag_contribs[:10],
        "performer_penalties": perf_pens,
        "tag_penalty": round(tag_pen, 4),
        "performer_penalty": round(perf_pen, 4),
    }
