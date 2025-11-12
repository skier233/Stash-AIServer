from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence, Tuple

from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.recommendations.registry import recommender
from stash_ai_server.recommendations.utils.scene_fetch import fetch_scenes_by_ids
from stash_ai_server.recommendations.utils.tag_profiles import build_watched_tag_profile
from stash_ai_server.recommendations.utils.timespan_metrics import collect_tag_durations

_log = logging.getLogger(__name__)

DEFAULT_SERVICE = "AI_Tagging"
DEFAULT_TAG_LIMIT = 12
DEFAULT_CANDIDATE_POOL = 300
DEFAULT_MIN_WATCHED_SECONDS = 10.0
TAG_PROFILE_MODE_SEGMENTS = "watched_segments"
TAG_PROFILE_MODE_SCENE = "full_scene"
DEFAULT_TAG_SOURCE_MODE = TAG_PROFILE_MODE_SEGMENTS


def _coerce_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback

@recommender(
    id="segment_similarity",
    label="Segment Similarity",
    description="Recommend scenes whose tagged segments overlap with the parts the user actually watched.",
    contexts=[RecContext.similar_scene],
    config=[
        {
            "name": "tag_limit",
            "label": "Dominant Tag Limit",
            "type": "number",
            "default": DEFAULT_TAG_LIMIT,
            "min": 3,
            "max": 40,
        },
        {
            "name": "candidate_pool",
            "label": "Candidate Pool",
            "type": "number",
            "default": DEFAULT_CANDIDATE_POOL,
            "min": 40,
            "max": 800,
        },
        {
            "name": "tag_source_mode",
            "label": "Tag Source",
            "type": "select",
            "default": DEFAULT_TAG_SOURCE_MODE,
            "options": [
                {"value": TAG_PROFILE_MODE_SEGMENTS, "label": "Watched Segments"},
                {"value": TAG_PROFILE_MODE_SCENE, "label": "Full Scene Tags"},
            ],
            "help": "Choose whether the profile is built from watched segments or the scene's full tag durations.",
        },
        {
            "name": "min_watched_seconds",
            "label": "Require Watched Seconds",
            "type": "number",
            "default": DEFAULT_MIN_WATCHED_SECONDS,
            "min": 0,
            "max": 600,
        },
    ],
    supports_pagination=True,
    exposes_scores=True,
    needs_seed_scenes=True,
    allows_multi_seed=True,
)
async def segment_similarity(ctx: Dict[str, Any], request: RecommendationRequest):
    cfg = request.config or {}

    # Resolve the set of seed scenes the caller provided. We exit early when none are
    # supplied because the recommender needs at least one watched reference scene.
    seed_ids = [int(sid) for sid in request.seedSceneIds or [] if sid is not None]
    if not seed_ids:
        return {"scenes": [], "total": 0, "has_more": False}

    # Normalise configuration knobs with sensible bounds so the downstream logic is
    # working with floats/ints. ``_coerce_*`` helpers swallow invalid user input.
    service_name = DEFAULT_SERVICE
    tag_limit = max(1, _coerce_int(cfg.get("tag_limit"), DEFAULT_TAG_LIMIT))
    candidate_pool = max(20, _coerce_int(cfg.get("candidate_pool"), DEFAULT_CANDIDATE_POOL))
    tag_source_mode = cfg.get("tag_source_mode") or DEFAULT_TAG_SOURCE_MODE
    if tag_source_mode not in {TAG_PROFILE_MODE_SEGMENTS, TAG_PROFILE_MODE_SCENE}:
        tag_source_mode = DEFAULT_TAG_SOURCE_MODE
    min_watched_seconds = max(0.0, _coerce_float(cfg.get("min_watched_seconds"), DEFAULT_MIN_WATCHED_SECONDS))

    _log.debug(
        "segment_similarity: resolved configuration seed_count=%s tag_limit=%s pool=%s mode=%s min_watch=%s",
        len(seed_ids),
        tag_limit,
        candidate_pool,
        tag_source_mode,
        round(min_watched_seconds, 3),
    )

    # ``build_watched_tag_profile`` returns
    # (aggregated_tag_seconds, total_watched_seconds, per_scene_breakdown). The
    # ``prefer_full_scene`` flag toggles whether we rely on watched segments or fall back
    # to whole-scene tag totals when segments are missing.
    tag_profile, total_watched, seed_breakdown = build_watched_tag_profile(
        service=service_name,
        scene_ids=seed_ids,
        prefer_full_scene=tag_source_mode == TAG_PROFILE_MODE_SCENE,
    )

    _log.debug("segment_similarity: tag profile %s", tag_profile)
    _log.debug("segment_similarity: total watched %s", total_watched)
    _log.debug("segment_similarity: seed breakdown %s", seed_breakdown)
    if not tag_profile:
        _log.info("segment_similarity: no tag coverage derived from watch data")
        return {"scenes": [], "total": 0, "has_more": False}

    overlap_threshold = min_watched_seconds

    ordered_tags = sorted(tag_profile.items(), key=lambda item: item[1], reverse=True)
    if tag_limit > 0 and len(ordered_tags) > tag_limit:
        ordered_tags = ordered_tags[:tag_limit]
    active_tags = dict(ordered_tags)

    tag_ids = list(active_tags.keys())

    normalization_base = total_watched if total_watched > 0 else sum(active_tags.values())
    if normalization_base <= 0:
        normalization_base = 1.0

    # ``collect_tag_durations`` provides a candidate index shaped as
    # ``{scene_id: {tag_id: duration_seconds}}`` for every tag in ``tag_ids``. This lets
    # us scan potential recommendations without additional queries per scene.
    tag_duration_index = collect_tag_durations(service=service_name, tag_ids=tag_ids)
    if not tag_duration_index:
        _log.info("segment_similarity: no candidate durations for active tags")
        return {"scenes": [], "total": 0, "has_more": False}

    seed_set = set(seed_ids)
    candidate_scores: List[Tuple[int, float, float, List[Tuple[int, float, float, float]]]] = []

    # Iterate through every candidate scene and compute tag overlap (seconds) between the
    # watched tag profile and the candidate's tag durations. "contributions" stores
    # debug tuples of (tag_id, overlap_s, candidate_s, watched_s).
    for scene_id, candidate_tag_map in tag_duration_index.items():
        if scene_id in seed_set:
            continue
        overlap_seconds = 0.0
        contributions: List[Tuple[int, float, float, float]] = []
        for tag_id, watched_duration in active_tags.items():
            candidate_duration = float(candidate_tag_map.get(tag_id, 0.0))
            if candidate_duration <= 0:
                continue
            overlap = min(candidate_duration, watched_duration)
            if overlap <= 0:
                continue
            contributions.append((tag_id, overlap, candidate_duration, watched_duration))
            overlap_seconds += overlap
        if not contributions:
            continue

        if overlap_threshold > 0 and overlap_seconds < overlap_threshold:
            continue
        normalized_score = overlap_seconds / normalization_base
        candidate_scores.append((scene_id, normalized_score, overlap_seconds, contributions))

    if not candidate_scores:
        return {"scenes": [], "total": 0, "has_more": False}

    candidate_scores.sort(key=lambda item: (item[1], item[2]), reverse=True)
    if candidate_pool > 0 and len(candidate_scores) > candidate_pool:
        candidate_scores = candidate_scores[:candidate_pool]

    requested_offset = request.offset if isinstance(request.offset, int) and request.offset is not None else 0
    if requested_offset < 0:
        requested_offset = 0
    requested_limit = request.limit if isinstance(request.limit, int) and request.limit and request.limit > 0 else 40

    total_candidates = len(candidate_scores)
    page_slice = candidate_scores[requested_offset : requested_offset + requested_limit]
    if not page_slice:
        return {"scenes": [], "total": total_candidates, "has_more": False}

    # ``fetch_scenes_by_ids`` returns the hydrated payloads for the paged candidate ids
    # so we can attach scores and debug metadata in-place.
    scene_ids = [scene_id for scene_id, *_ in page_slice]
    scene_payloads = fetch_scenes_by_ids(scene_ids)
    results: List[Dict[str, Any]] = []

    for scene_id, normalized_score, overlap_seconds, contributions in page_slice:
        payload = scene_payloads.get(scene_id)
        if payload is None:
            continue
        score_value = max(0.0, min(1.0, normalized_score))
        payload["score"] = round(score_value, 4)
        tag_debug = [
            {
                "tag_id": tag_id,
                "overlap_seconds": round(overlap, 3),
                "candidate_seconds": round(candidate_seconds, 3),
                "watched_seconds": round(watched_seconds, 3),
            }
            for tag_id, overlap, candidate_seconds, watched_seconds in sorted(contributions, key=lambda item: item[1], reverse=True)[:6]
        ]
        debug_meta = payload.setdefault("debug_meta", {})
        debug_meta["segment_similarity"] = {
            "normalized_score": round(score_value, 4),
            "overlap_seconds": round(overlap_seconds, 3),
            "tag_hits": tag_debug,
            "normalization_base": round(normalization_base, 3),
            "watch_seconds": round(total_watched, 3),
            "tag_source_mode": tag_source_mode,
        }
        results.append(payload)

    has_more = requested_offset + len(page_slice) < total_candidates
    return {"scenes": results, "total": total_candidates, "has_more": has_more}
