from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence, Tuple

from stash_ai_server.db.ai_results_store import get_scene_tag_totals
from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.recommendations.registry import recommender
from stash_ai_server.recommendations.utils.scene_fetch import fetch_scenes_by_ids
from stash_ai_server.recommendations.utils.timespan_metrics import (
    collect_tag_durations,
    collect_watched_segment_tag_durations,
)

_log = logging.getLogger(__name__)

DEFAULT_SERVICE = "AI_Tagging"
DEFAULT_TAG_LIMIT = 12
DEFAULT_CANDIDATE_POOL = 300
DEFAULT_MIN_WATCHED_SECONDS = 10.0


def _summarize_durations(duration_map: Dict[int, float] | None, *, limit: int = 5) -> List[Dict[str, float]]:
    if not duration_map:
        return []
    ordered = sorted(duration_map.items(), key=lambda item: item[1], reverse=True)
    summary = []
    for tag_id, value in ordered[:limit]:
        summary.append({"tag_id": int(tag_id), "seconds": round(float(value), 3)})
    return summary


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


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _build_tag_profile(
    *,
    service: str,
    seed_ids: Sequence[int],
    allow_fallback: bool,
) -> Tuple[Dict[int, float], float, Dict[int, Dict[str, Any]]]:
    aggregated: Dict[int, float] = {}
    total_watched = 0.0
    seed_details: Dict[int, Dict[str, Any]] = {}

    for scene_id in seed_ids:
        detail = seed_details.setdefault(
            scene_id,
            {"watch_seconds": 0.0, "watch_tags": {}, "fallback_tags": {}},
        )
        watched_map, watched_total = collect_watched_segment_tag_durations(
            service=service,
            scene_id=scene_id,
        )
        if watched_total > 0:
            total_watched += watched_total
        detail["watch_seconds"] = float(watched_total or 0.0)
        if watched_map:
            detail["watch_tags"] = {int(tag_id): float(duration) for tag_id, duration in watched_map.items()}
            for tag_id, duration in watched_map.items():
                tag_key = int(tag_id)
                aggregated[tag_key] = aggregated.get(tag_key, 0.0) + float(duration)
            continue
        if not allow_fallback:
            continue
        fallback_map = get_scene_tag_totals(service=service, scene_id=scene_id)
        if not fallback_map:
            continue
        detail["fallback_tags"] = {
            int(tag_id): float(duration)
            for tag_id, duration in fallback_map.items()
            if duration not in (None, "")
        }
        for tag_id, duration in fallback_map.items():
            tag_key = int(tag_id)
            aggregated[tag_key] = aggregated.get(tag_key, 0.0) + float(duration)

    return aggregated, total_watched, seed_details


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
            "name": "allow_full_scene_fallback",
            "label": "Fallback to Full Scene Tags",
            "type": "boolean",
            "default": True,
            "help": "Use full-scene tag durations when watch segments are unavailable.",
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

    seed_ids = [int(sid) for sid in request.seedSceneIds or [] if sid is not None]
    if not seed_ids:
        return {"scenes": [], "total": 0, "has_more": False}

    service_name = DEFAULT_SERVICE
    tag_limit = max(1, _coerce_int(cfg.get("tag_limit"), DEFAULT_TAG_LIMIT))
    candidate_pool = max(20, _coerce_int(cfg.get("candidate_pool"), DEFAULT_CANDIDATE_POOL))
    allow_fallback = _normalize_bool(cfg.get("allow_full_scene_fallback"), default=True)
    min_watched_seconds = max(0.0, _coerce_float(cfg.get("min_watched_seconds"), DEFAULT_MIN_WATCHED_SECONDS))

    _log.debug(
        "segment_similarity: resolved configuration",
        extra={
            "seed_count": len(seed_ids),
            "tag_limit": tag_limit,
            "candidate_pool": candidate_pool,
            "allow_fallback": allow_fallback,
            "min_watched_seconds": round(min_watched_seconds, 3),
        },
    )

    tag_profile, total_watched, seed_breakdown = _build_tag_profile(
        service=service_name,
        seed_ids=seed_ids,
        allow_fallback=allow_fallback,
    )

    _log.debug("segment_similarity: tag profile %s", tag_profile)
    _log.debug("segment_similarity: total watched %s", total_watched)
    _log.debug("segment_similarity: seed breakdown %s", seed_breakdown)

    if seed_breakdown:
        seed_summary = []
        for scene_id in seed_ids:
            detail = seed_breakdown.get(scene_id, {})
            seed_summary.append(
                {
                    "scene_id": scene_id,
                    "watch_seconds": round(float(detail.get("watch_seconds", 0.0)), 3),
                    "watched_tags": _summarize_durations(detail.get("watch_tags"), limit=6),
                    "fallback_tags": _summarize_durations(detail.get("fallback_tags"), limit=4) if allow_fallback else [],
                }
            )
        _log.info("segment_similarity: seed coverage summary", extra={"seeds": seed_summary})

    if not tag_profile:
        _log.info("segment_similarity: no tag coverage derived from watch data", extra={"seed_count": len(seed_ids)})
        return {"scenes": [], "total": 0, "has_more": False}

    _log.debug(
        "segment_similarity: built tag profile",
        extra={
            "profile_size": len(tag_profile),
            "total_watched_seconds": round(total_watched, 3),
        },
    )

    normalization_base = total_watched if total_watched > 0 else sum(tag_profile.values())
    if normalization_base <= 0:
        normalization_base = 1.0
    overlap_threshold = min_watched_seconds if total_watched > 0 else 0.0

    ordered_tags = sorted(tag_profile.items(), key=lambda item: item[1], reverse=True)
    if tag_limit > 0 and len(ordered_tags) > tag_limit:
        ordered_tags = ordered_tags[:tag_limit]
    active_tags = dict(ordered_tags)

    _log.info(
        "segment_similarity: active tag selection",
        extra={
            "active_tag_count": len(active_tags),
            "top_tags": _summarize_durations(active_tags, limit=8),
            "normalization_base": round(normalization_base, 3),
            "overlap_threshold": round(overlap_threshold, 3),
        },
    )

    tag_ids = list(active_tags.keys())
    tag_duration_index = collect_tag_durations(service=service_name, tag_ids=tag_ids)
    if not tag_duration_index:
        _log.info("segment_similarity: no candidate durations for active tags", extra={"tag_count": len(tag_ids)})
        return {"scenes": [], "total": 0, "has_more": False}

    _log.info(
        "segment_similarity: candidate search space",
        extra={
            "candidate_scene_count": len(tag_duration_index),
            "tag_count": len(tag_ids),
        },
    )

    seed_set = set(seed_ids)
    candidate_scores: List[Tuple[int, float, float, List[Tuple[int, float, float, float]]]] = []

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

    preview = []
    for scene_id, normalized_score, overlap_seconds, contributions in candidate_scores[:5]:
        preview.append(
            {
                "scene_id": scene_id,
                "score": round(normalized_score, 4),
                "overlap_seconds": round(overlap_seconds, 3),
                "top_overlaps": [
                    {
                        "tag_id": tag_id,
                        "overlap": round(overlap, 3),
                    }
                    for tag_id, overlap, *_ in sorted(contributions, key=lambda item: item[1], reverse=True)[:4]
                ],
            }
        )
    _log.info(
        "segment_similarity: scored candidate preview",
        extra={
            "candidate_count": len(candidate_scores),
            "preview": preview,
        },
    )

    requested_offset = request.offset if isinstance(request.offset, int) and request.offset is not None else 0
    if requested_offset < 0:
        requested_offset = 0
    requested_limit = request.limit if isinstance(request.limit, int) and request.limit and request.limit > 0 else 40

    total_candidates = len(candidate_scores)
    page_slice = candidate_scores[requested_offset : requested_offset + requested_limit]
    if not page_slice:
        return {"scenes": [], "total": total_candidates, "has_more": False}

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
        }
        results.append(payload)

    has_more = requested_offset + len(page_slice) < total_candidates
    result_preview = [
        {
            "scene_id": payload.get("id"),
            "score": payload.get("score"),
            "overlap_seconds": payload.get("debug_meta", {})
            .get("segment_similarity", {})
            .get("overlap_seconds"),
        }
        for payload in results[:3]
    ]
    _log.info(
        "segment_similarity: returning page",
        extra={
            "page_size": len(results),
            "total_candidates": total_candidates,
            "offset": requested_offset,
            "limit": requested_limit,
            "has_more": has_more,
            "preview": result_preview,
        },
    )
    return {"scenes": results, "total": total_candidates, "has_more": has_more}
