"""AI tag duration-based similarity for the recommendation system.

Uses the temporal tag data from AI tagging (tag durations in seconds per scene)
to compute richer content similarity than binary Stash tags.  Key advantage:
a tag appearing for 200 seconds is weighted much more than one appearing for 2.

Provides two modes:
  - **taste** (global_feed): Build a duration-weighted user taste vector from
    watched scenes and score candidates via cosine similarity.
  - **seed** (similar_scene): Build a profile from seed scenes and score
    candidates the same way.

All functions gracefully return empty results when no AI tagging data exists.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from stash_ai_server.recommendations.utils.tag_profiles import (
    fetch_tag_durations_for_scenes,
    fetch_tag_document_frequencies,
    fetch_total_tagged_scene_count,
)

_log = logging.getLogger(__name__)

DEFAULT_SERVICE = "AI_Tagging"


# ---------------------------------------------------------------------------
# Vector operations
# ---------------------------------------------------------------------------

def _build_duration_idf(
    tag_ids: Set[int],
    *,
    service: str = DEFAULT_SERVICE,
) -> Dict[int, float]:
    """Compute smoothed IDF for AI tags: ``log((1+N)/(1+df)) + 1``.

    Uses the AI results corpus (scenes that have been AI-tagged) rather than
    the full Stash library.
    """
    if not tag_ids:
        return {}
    corpus_size = fetch_total_tagged_scene_count(service=service)
    if corpus_size <= 0:
        return {tid: 1.0 for tid in tag_ids}
    df_map = fetch_tag_document_frequencies(service=service, tag_ids=list(tag_ids))
    n = max(corpus_size, 1)
    return {
        tid: math.log((1.0 + n) / (1.0 + df_map.get(tid, 0))) + 1.0
        for tid in tag_ids
    }


def _build_duration_vector(
    durations: Mapping[int, float],
    idf: Mapping[int, float],
) -> Dict[int, float]:
    """Build an L2-normalized TF-IDF vector using log(1+duration) as TF.

    Using log-scaled duration prevents very long tags from dominating while
    still reflecting that more screen time = more important.
    """
    raw: Dict[int, float] = {}
    for tid, dur in durations.items():
        if dur <= 0:
            continue
        tf = math.log1p(dur)  # log(1 + duration_seconds)
        raw[tid] = tf * idf.get(tid, 1.0)
    if not raw:
        return {}
    magnitude = math.sqrt(sum(v * v for v in raw.values()))
    if magnitude <= 0:
        return {}
    return {tid: v / magnitude for tid, v in raw.items()}


def _cosine_sim(
    vec_a: Mapping[int, float],
    vec_b: Mapping[int, float],
) -> float:
    """Dot product of two L2-normalized sparse vectors (= cosine similarity)."""
    if not vec_a or not vec_b:
        return 0.0
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a
    return sum(v * vec_b[k] for k, v in vec_a.items() if k in vec_b)


# ---------------------------------------------------------------------------
# Profile builders
# ---------------------------------------------------------------------------

def build_ai_tag_taste_profile(
    *,
    watched_scene_ids: Sequence[int],
    engagement_scores: Mapping[int, float] | None = None,
    service: str = DEFAULT_SERVICE,
) -> Tuple[Dict[int, float], Dict[int, float], int]:
    """Build a duration-weighted user taste vector from watched scenes.

    Returns ``(profile_vector, idf, corpus_size)`` where:
    - ``profile_vector`` is an L2-normalized vector suitable for cosine similarity
    - ``idf`` maps tag IDs to IDF weights (needed for candidate scoring)
    - ``corpus_size`` is the number of AI-tagged scenes in the corpus

    Each watched scene's tag durations are weighted by engagement score before
    aggregation.
    """
    if not watched_scene_ids:
        return {}, {}, 0

    per_scene, tag_ids = fetch_tag_durations_for_scenes(
        service=service,
        scene_ids=list(watched_scene_ids),
    )
    if not per_scene:
        return {}, {}, 0

    # Aggregate across watched scenes, weighted by engagement
    aggregated: Dict[int, float] = defaultdict(float)
    for scene_id, tag_durs in per_scene.items():
        weight = 1.0
        if engagement_scores:
            weight = max(engagement_scores.get(scene_id, 0.0), 0.0)
            if weight <= 0:
                continue
        for tid, dur in tag_durs.items():
            if dur > 0:
                aggregated[tid] += math.log1p(dur) * weight

    if not aggregated:
        return {}, {}, 0

    idf = _build_duration_idf(tag_ids, service=service)
    corpus_size = fetch_total_tagged_scene_count(service=service)

    # Apply IDF and normalize
    raw: Dict[int, float] = {}
    for tid, val in aggregated.items():
        raw[tid] = val * idf.get(tid, 1.0)

    magnitude = math.sqrt(sum(v * v for v in raw.values()))
    if magnitude <= 0:
        return {}, idf, corpus_size
    profile = {tid: v / magnitude for tid, v in raw.items()}

    return profile, idf, corpus_size


def build_ai_tag_seed_profile(
    *,
    seed_scene_ids: Sequence[int],
    service: str = DEFAULT_SERVICE,
) -> Tuple[Dict[int, float], Dict[int, float], int]:
    """Build an AI tag profile from seed scenes (equal weight per scene).

    Returns ``(profile_vector, idf, corpus_size)``.
    """
    return build_ai_tag_taste_profile(
        watched_scene_ids=seed_scene_ids,
        engagement_scores=None,
        service=service,
    )


# ---------------------------------------------------------------------------
# Candidate scoring
# ---------------------------------------------------------------------------

def score_candidates_by_ai_tags(
    *,
    candidate_scene_ids: Sequence[int],
    profile: Mapping[int, float],
    idf: Mapping[int, float],
    service: str = DEFAULT_SERVICE,
    return_detail: bool = False,
) -> Dict[int, float] | Tuple[Dict[int, float], Dict[int, List[Dict[str, Any]]]]:
    """Score candidate scenes against an AI tag profile.

    Returns ``{scene_id: similarity}`` where similarity is in [0, 1].
    Only scenes that have AI tagging data AND nonzero similarity are included.

    If *return_detail* is True, also returns per-scene contribution lists:
    ``{scene_id: [{tag_id, contribution, profile_w, scene_w}, ...]}``
    """
    if not candidate_scene_ids or not profile:
        return ({}, {}) if return_detail else {}

    per_scene, _ = fetch_tag_durations_for_scenes(
        service=service,
        scene_ids=list(candidate_scene_ids),
    )
    if not per_scene:
        return ({}, {}) if return_detail else {}

    scores: Dict[int, float] = {}
    detail: Dict[int, List[Dict[str, Any]]] = {}
    for scene_id, tag_durs in per_scene.items():
        vec = _build_duration_vector(tag_durs, idf)
        sim = _cosine_sim(profile, vec)
        if sim > 0:
            scores[scene_id] = sim
            if return_detail:
                # Per-tag dot-product contributions
                contribs = []
                for tid in vec:
                    if tid in profile:
                        c = profile[tid] * vec[tid]
                        contribs.append({
                            "tag_id": tid,
                            "contribution": round(c, 6),
                            "profile_w": round(profile[tid], 6),
                            "scene_w": round(vec[tid], 6),
                            "duration": round(tag_durs.get(tid, 0.0), 1),
                        })
                contribs.sort(key=lambda x: x["contribution"], reverse=True)
                detail[scene_id] = contribs[:12]

    if return_detail:
        return scores, detail
    return scores


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def compute_ai_tag_similarity(
    *,
    scene_ids: Sequence[int] | None = None,
    watched_scene_ids: Sequence[int] | None = None,
    engagement_scores: Mapping[int, float] | None = None,
    candidate_scene_ids: Sequence[int],
    mode: str = "taste",
    service: str = DEFAULT_SERVICE,
    return_detail: bool = False,
) -> Dict[int, float] | Tuple[Dict[int, float], Dict[int, List[Dict[str, Any]]]]:
    """Compute AI tag duration-weighted similarity scores for candidates.

    Parameters
    ----------
    scene_ids : list[int] | None
        Seed scene IDs (for ``mode="seed"`` / similar_scene).
    watched_scene_ids : list[int] | None
        Watched scene IDs (for ``mode="taste"`` / global_feed).
    engagement_scores : dict | None
        Engagement scores for weighting the taste profile.
    candidate_scene_ids : list[int]
        Scene IDs to score against the profile.
    mode : str
        ``"seed"`` for similar_scene, ``"taste"`` for global_feed.
    service : str
        AI tagging service name.
    return_detail : bool
        If True, returns ``(scores, detail)`` with per-tag contribution data.

    Returns
    -------
    dict[int, float] or (dict, dict)
        ``{scene_id: similarity}`` where similarity ∈ [0, 1].
        If return_detail, also ``{scene_id: [{tag_id, contribution, ...}]}``.
    """
    if mode == "seed" and scene_ids:
        profile, idf, corpus_size = build_ai_tag_seed_profile(
            seed_scene_ids=scene_ids,
            service=service,
        )
    elif mode == "taste" and watched_scene_ids:
        profile, idf, corpus_size = build_ai_tag_taste_profile(
            watched_scene_ids=watched_scene_ids,
            engagement_scores=engagement_scores,
            service=service,
        )
    else:
        return ({}, {}) if return_detail else {}

    if not profile:
        _log.debug("ai_tag_similarity: no AI tag profile built (mode=%s)", mode)
        return ({}, {}) if return_detail else {}

    result = score_candidates_by_ai_tags(
        candidate_scene_ids=candidate_scene_ids,
        profile=profile,
        idf=idf,
        service=service,
        return_detail=return_detail,
    )

    scores = result[0] if return_detail else result

    if scores:
        _log.info(
            "ai_tag_similarity: scored %d candidates (mode=%s, profile_tags=%d, corpus=%d)",
            len(scores), mode, len(profile), corpus_size,
        )
    return result
