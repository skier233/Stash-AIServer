from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Set, Tuple

from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.recommendations.registry import recommender
from stash_ai_server.recommendations.utils.scene_fetch import (
    fetch_scene_candidates_by_performers,
    fetch_scenes_by_ids,
)
from stash_ai_server.recommendations.utils.tag_profiles import fetch_tag_durations_for_scenes
from stash_ai_server.recommendations.utils.timespan_metrics import collect_tag_durations
from stash_ai_server.recommendations.utils.watch_history import load_watch_history_summary

_log = logging.getLogger(__name__)

DEFAULT_SERVICE = "AI_Tagging"
DEFAULT_RECENT_DAYS = 60
DEFAULT_HISTORY_LIMIT = 200
DEFAULT_MIN_WATCH_SECONDS = 15.0
DEFAULT_TAG_PROFILE_LIMIT = 18
DEFAULT_CANDIDATE_POOL = 250
DEFAULT_PERFORMER_WEIGHT = 0.6
DEFAULT_TAG_WEIGHT = 0.3
DEFAULT_STUDIO_BONUS = 0.05
DEFAULT_SERIES_BONUS = 0.05


def _summarize_scores(value_map: Dict[int, float] | None, *, limit: int = 6) -> List[Dict[str, float]]:
    if not value_map:
        return []
    ordered = sorted(value_map.items(), key=lambda item: item[1], reverse=True)
    preview: List[Dict[str, float]] = []
    for key, value in ordered[:limit]:
        preview.append({"id": int(key), "score": round(float(value), 4)})
    return preview


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


def _normalize_seed_values(payloads: Dict[int, Dict[str, Any]]) -> Tuple[Set[int], Set[int], Set[int]]:
    performer_ids: Set[int] = set()
    studio_ids: Set[int] = set()
    series_ids: Set[int] = set()
    for scene in payloads.values():
        for performer in scene.get("performers", []):
            pid = performer.get("id")
            if pid is not None:
                try:
                    performer_ids.add(int(pid))
                except (TypeError, ValueError):
                    continue
        studio = scene.get("studio") or {}
        studio_id = studio.get("id") if isinstance(studio, dict) else None
        if studio_id is not None:
            try:
                studio_ids.add(int(studio_id))
            except (TypeError, ValueError):
                pass
        for series in scene.get("series", []) or []:
            sid = series.get("id") if isinstance(series, dict) else None
            if sid is None:
                continue
            try:
                series_ids.add(int(sid))
            except (TypeError, ValueError):
                continue
    return performer_ids, studio_ids, series_ids


def _normalize_datetime(days: float) -> datetime | None:
    if days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _build_tag_profile(
    *,
    service: str,
    history_summary: List[Dict[str, Any]],
    profile_limit: int,
) -> Dict[int, float]:
    if not history_summary:
        return {}

    scene_ids = [entry["scene_id"] for entry in history_summary]
    per_scene_tags, _ = fetch_tag_durations_for_scenes(service=service, scene_ids=scene_ids)
    if not per_scene_tags:
        return {}

    watched_lookup = {entry["scene_id"]: float(entry.get("watched_s") or 0.0) for entry in history_summary}
    aggregated: Dict[int, float] = {}

    for scene_id, tag_map in per_scene_tags.items():
        watched_total = watched_lookup.get(scene_id, 0.0)
        for tag_id, duration in tag_map.items():
            duration_val = float(duration or 0.0)
            if duration_val <= 0:
                continue
            if watched_total > 0:
                contribution = min(duration_val, watched_total)
            else:
                contribution = duration_val
            aggregated[tag_id] = aggregated.get(tag_id, 0.0) + contribution

    if not aggregated:
        return {}

    ordered = sorted(aggregated.items(), key=lambda item: item[1], reverse=True)
    if profile_limit > 0 and len(ordered) > profile_limit:
        ordered = ordered[:profile_limit]
    return dict(ordered)


def _score_performer_tag_candidate(
    *,
    scene_id: int,
    candidate_payload: Dict[str, Any],
    matched_performers: Set[int],
    performer_weight: float,
    seed_performers: Set[int],
    tag_profile: Dict[int, float],
    tag_overlap_map: Dict[int, Dict[int, float]],
    tag_weight: float,
    studio_ids: Set[int],
    studio_bonus: float,
    series_ids: Set[int],
    series_bonus: float,
) -> Tuple[float, Dict[str, Any]]:
    performer_component = 0.0
    if seed_performers:
        unit = performer_weight / len(seed_performers)
        performer_component = unit * len(matched_performers)

    candidate_tags = tag_overlap_map.get(scene_id, {}) if tag_profile else {}
    tag_component = 0.0
    tag_hits: List[Dict[str, Any]] = []
    overlap_sum = 0.0
    if tag_profile and candidate_tags:
        normalization = sum(tag_profile.values()) or 1.0
        for tag_id, watched_value in tag_profile.items():
            candidate_duration = float(candidate_tags.get(tag_id, 0.0))
            if candidate_duration <= 0:
                continue
            overlap = min(candidate_duration, watched_value)
            if overlap <= 0:
                continue
            overlap_sum += overlap
            tag_hits.append(
                {
                    "tag_id": tag_id,
                    "overlap_seconds": round(overlap, 3),
                    "candidate_seconds": round(candidate_duration, 3),
                    "watched_seconds": round(watched_value, 3),
                }
            )
        if overlap_sum > 0:
            tag_component = tag_weight * (overlap_sum / normalization)

    studio_component = 0.0
    studio = candidate_payload.get("studio") or {}
    studio_id = studio.get("id") if isinstance(studio, dict) else None
    if studio_id is not None and studio_ids and int(studio_id) in studio_ids:
        studio_component = studio_bonus

    series_component = 0.0
    if series_ids:
        for series in candidate_payload.get("series", []) or []:
            sid = series.get("id") if isinstance(series, dict) else None
            if sid is None:
                continue
            try:
                if int(sid) in series_ids:
                    series_component = series_bonus
                    break
            except (TypeError, ValueError):
                continue

    score = performer_component + tag_component + studio_component + series_component
    debug = {
        "performer_component": round(performer_component, 4),
        "tag_component": round(tag_component, 4),
        "tag_overlap_seconds": round(overlap_sum, 3),
        "studio_bonus": round(studio_component, 4) if studio_component else 0.0,
        "series_bonus": round(series_component, 4) if series_component else 0.0,
        "performer_matches": sorted(int(pid) for pid in matched_performers),
        "tag_hits": tag_hits[:6],
    }
    return score, debug


@recommender(
    id="performer_tag_hybrid",
    label="Performer + Tag Hybrid",
    description="Blend performer overlap with historical tag preferences plus studio/series cues.",
    contexts=[RecContext.similar_scene],
    config=[
        {
            "name": "recent_days",
            "label": "Watch History Window",
            "type": "number",
            "default": DEFAULT_RECENT_DAYS,
            "min": 0,
            "max": 365,
        },
        {
            "name": "history_limit",
            "label": "History Scene Limit",
            "type": "number",
            "default": DEFAULT_HISTORY_LIMIT,
            "min": 25,
            "max": 600,
        },
        {
            "name": "min_watch_seconds",
            "label": "Min Watched Seconds",
            "type": "number",
            "default": DEFAULT_MIN_WATCH_SECONDS,
            "min": 0,
            "max": 600,
        },
        {
            "name": "tag_profile_limit",
            "label": "Tag Profile Limit",
            "type": "number",
            "default": DEFAULT_TAG_PROFILE_LIMIT,
            "min": 5,
            "max": 60,
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
            "name": "performer_weight",
            "label": "Performer Weight",
            "type": "number",
            "default": DEFAULT_PERFORMER_WEIGHT,
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        {
            "name": "tag_weight",
            "label": "Tag Weight",
            "type": "number",
            "default": DEFAULT_TAG_WEIGHT,
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        {
            "name": "studio_bonus",
            "label": "Studio Bonus",
            "type": "number",
            "default": DEFAULT_STUDIO_BONUS,
            "min": 0,
            "max": 0.3,
            "step": 0.01,
        },
        {
            "name": "series_bonus",
            "label": "Series Bonus",
            "type": "number",
            "default": DEFAULT_SERIES_BONUS,
            "min": 0,
            "max": 0.3,
            "step": 0.01,
        },
    ],
    supports_pagination=True,
    exposes_scores=True,
    needs_seed_scenes=True,
    allows_multi_seed=True,
)
async def performer_tag_hybrid(ctx: Dict[str, Any], request: RecommendationRequest):
    cfg = request.config or {}

    seed_ids = [int(sid) for sid in request.seedSceneIds or [] if sid is not None]
    if not seed_ids:
        return {"scenes": [], "total": 0, "has_more": False}

    service_name = DEFAULT_SERVICE
    recent_days = _coerce_float(cfg.get("recent_days"), DEFAULT_RECENT_DAYS)
    history_limit = max(25, _coerce_int(cfg.get("history_limit"), DEFAULT_HISTORY_LIMIT))
    min_watch_seconds = max(0.0, _coerce_float(cfg.get("min_watch_seconds"), DEFAULT_MIN_WATCH_SECONDS))
    tag_profile_limit = max(1, _coerce_int(cfg.get("tag_profile_limit"), DEFAULT_TAG_PROFILE_LIMIT))
    candidate_pool = max(20, _coerce_int(cfg.get("candidate_pool"), DEFAULT_CANDIDATE_POOL))
    performer_weight = max(0.0, _coerce_float(cfg.get("performer_weight"), DEFAULT_PERFORMER_WEIGHT))
    tag_weight = max(0.0, _coerce_float(cfg.get("tag_weight"), DEFAULT_TAG_WEIGHT))
    studio_bonus = max(0.0, _coerce_float(cfg.get("studio_bonus"), DEFAULT_STUDIO_BONUS))
    series_bonus = max(0.0, _coerce_float(cfg.get("series_bonus"), DEFAULT_SERIES_BONUS))

    _log.info(
        "performer_tag_hybrid: resolved configuration",
        extra={
            "seed_count": len(seed_ids),
            "performer_weight": round(performer_weight, 4),
            "tag_weight": round(tag_weight, 4),
            "studio_bonus": round(studio_bonus, 4),
            "series_bonus": round(series_bonus, 4),
            "recent_days": round(recent_days, 2),
            "history_limit": history_limit,
            "candidate_pool": candidate_pool,
        },
    )

    seed_payloads = fetch_scenes_by_ids(seed_ids)
    if not seed_payloads:
        _log.info("performer_tag_hybrid: no seed payloads available", extra={"seed_ids": seed_ids})
        return {"scenes": [], "total": 0, "has_more": False}

    seed_performers, studio_ids, series_ids = _normalize_seed_values(seed_payloads)
    if not seed_performers:
        _log.info("performer_tag_hybrid: seed scenes do not list performers", extra={"seed_ids": seed_ids})

    _log.info(
        "performer_tag_hybrid: normalized seed context",
        extra={
            "seed_performers": len(seed_performers),
            "seed_studios": len(studio_ids),
            "seed_series": len(series_ids),
        },
    )

    recent_cutoff = _normalize_datetime(recent_days)
    history_summary = load_watch_history_summary(
        recent_cutoff=recent_cutoff,
        min_watch_seconds=min_watch_seconds,
        limit=history_limit,
    )

    watched_scene_ids = {entry["scene_id"] for entry in history_summary}
    exclude_set = watched_scene_ids | set(seed_ids)

    _log.info(
        "performer_tag_hybrid: loaded watch summary",
        extra={
            "history_rows": len(history_summary),
            "watched_overlap": len(watched_scene_ids & set(seed_ids)),
        },
    )

    tag_profile = _build_tag_profile(
        service=service_name,
        history_summary=history_summary,
        profile_limit=tag_profile_limit,
    ) if tag_weight > 0 else {}

    if tag_profile:
        _log.info(
            "performer_tag_hybrid: built tag profile",
            extra={
                "profile_tags": len(tag_profile),
                "top_tags": _summarize_scores(tag_profile, limit=8),
            },
        )
    else:
        _log.info("performer_tag_hybrid: tag profile empty", extra={"tag_weight": round(tag_weight, 4)})

    max_pre_score = candidate_pool * 3 if candidate_pool > 0 else None
    candidate_scene_ids: List[int] = []
    candidate_seen: Set[int] = set()
    matched_performer_map: Dict[int, Set[int]] = {}
    candidate_origin: Dict[int, Set[str]] = {}
    tag_candidate_strength: Dict[int, float] = {}

    def add_candidate(scene_id: int, matched: Set[int] | None, source: str, *, tag_strength: float | None = None) -> None:
        if scene_id in exclude_set:
            return
        if matched:
            matched_performer_map.setdefault(scene_id, set()).update(set(matched))
        else:
            matched_performer_map.setdefault(scene_id, set())
        if tag_strength is not None:
            tag_candidate_strength[scene_id] = max(tag_candidate_strength.get(scene_id, 0.0), tag_strength)
        candidate_origin.setdefault(scene_id, set()).add(source)
        if scene_id in candidate_seen:
            return
        if max_pre_score is not None and len(candidate_scene_ids) >= max_pre_score:
            return
        candidate_seen.add(scene_id)
        candidate_scene_ids.append(scene_id)

    performer_candidates: List[Tuple[int, Set[int]]] = []
    if seed_performers:
        performer_candidates = fetch_scene_candidates_by_performers(
            performer_ids=list(seed_performers),
            exclude_scene_ids=exclude_set,
            limit=candidate_pool * 2,
        )
        for scene_id, matched in performer_candidates:
            add_candidate(scene_id, matched, "performer")

    if not performer_candidates:
        _log.info(
            "performer_tag_hybrid: no performer-based candidates",
            extra={"seed_performers": len(seed_performers)},
        )

    tag_duration_index: Dict[int, Dict[int, float]] = {}
    tag_strength_items: List[Tuple[int, float]] = []
    if tag_profile and tag_weight > 0:
        tag_duration_index = collect_tag_durations(service=service_name, tag_ids=tag_profile.keys())
        for scene_id, tag_map in tag_duration_index.items():
            if scene_id in exclude_set:
                continue
            overlap = 0.0
            for tag_id, watched_value in tag_profile.items():
                candidate_duration = float(tag_map.get(tag_id, 0.0))
                if candidate_duration <= 0:
                    continue
                overlap += min(candidate_duration, watched_value)
            if overlap <= 0:
                continue
            tag_strength_items.append((scene_id, overlap))
        tag_strength_items.sort(key=lambda item: item[1], reverse=True)
        for scene_id, overlap in tag_strength_items:
            add_candidate(scene_id, None, "tag", tag_strength=overlap)
            if max_pre_score is not None and len(candidate_scene_ids) >= max_pre_score:
                break

    if not candidate_scene_ids:
        _log.info(
            "performer_tag_hybrid: no candidates after combining sources",
            extra={
                "seed_performers": len(seed_performers),
                "tag_profile_size": len(tag_profile),
                "history_rows": len(history_summary),
            },
        )
        return {"scenes": [], "total": 0, "has_more": False}

    for scene_id in candidate_scene_ids:
        matched_performer_map.setdefault(scene_id, set())
        if scene_id not in candidate_origin:
            candidate_origin[scene_id] = {"unknown"}

    candidate_tag_map = (
        {scene_id: tag_duration_index.get(scene_id, {}) for scene_id in candidate_scene_ids}
        if tag_profile and tag_weight > 0
        else {}
    )

    origin_counts: Dict[str, int] = {}
    for origins in candidate_origin.values():
        for origin in origins:
            origin_counts[origin] = origin_counts.get(origin, 0) + 1

    _log.info(
        "performer_tag_hybrid: assembled candidates",
        extra={
            "selected_candidates": len(candidate_scene_ids),
            "performer_candidates": len(performer_candidates),
            "tag_candidates_considered": len(tag_strength_items),
            "origin_counts": origin_counts,
        },
    )

    candidate_payloads = fetch_scenes_by_ids(candidate_scene_ids)
    scored_candidates: List[Tuple[int, float, Dict[str, Any]]] = []

    for scene_id in candidate_scene_ids:
        matched_performers = matched_performer_map.get(scene_id, set())
        payload = candidate_payloads.get(scene_id)
        if payload is None:
            continue
        score, debug = _score_performer_tag_candidate(
            scene_id=scene_id,
            candidate_payload=payload,
            matched_performers=matched_performers,
            performer_weight=performer_weight,
            seed_performers=seed_performers,
            tag_profile=tag_profile,
            tag_overlap_map=candidate_tag_map,
            tag_weight=tag_weight,
            studio_ids=studio_ids,
            studio_bonus=studio_bonus,
            series_ids=series_ids,
            series_bonus=series_bonus,
        )
        if score <= 0:
            continue
        scored_candidates.append((scene_id, score, debug))

    if not scored_candidates:
        return {"scenes": [], "total": 0, "has_more": False}

    scored_candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
    if len(scored_candidates) > candidate_pool:
        scored_candidates = scored_candidates[:candidate_pool]

    preview = [
        {
            "scene_id": scene_id,
            "score": round(score, 4),
            "performer_matches": len(scored_debug.get("performer_matches", [])),
            "tag_overlap_seconds": scored_debug.get("tag_overlap_seconds"),
        }
        for scene_id, score, scored_debug in scored_candidates[:5]
    ]
    _log.info(
        "performer_tag_hybrid: scored candidates",
        extra={
            "scored_candidate_count": len(scored_candidates),
            "preview": preview,
        },
    )

    requested_offset = request.offset if isinstance(request.offset, int) and request.offset is not None else 0
    if requested_offset < 0:
        requested_offset = 0
    requested_limit = request.limit if isinstance(request.limit, int) and request.limit and request.limit > 0 else 40

    total_candidates = len(scored_candidates)
    page_slice = scored_candidates[requested_offset : requested_offset + requested_limit]
    if not page_slice:
        return {"scenes": [], "total": total_candidates, "has_more": False}

    results: List[Dict[str, Any]] = []
    for scene_id, score, debug in page_slice:
        payload = candidate_payloads.get(scene_id)
        if payload is None:
            continue
        clamped_score = max(0.0, min(1.0, score))
        payload["score"] = round(clamped_score, 4)
        debug_meta = payload.setdefault("debug_meta", {})
        sources = sorted(candidate_origin.get(scene_id, {"unknown"}))
        if sources:
            debug["candidate_sources"] = sources
        pre_overlap = tag_candidate_strength.get(scene_id)
        if pre_overlap:
            debug["preselect_tag_overlap"] = round(pre_overlap, 3)
        debug_meta["performer_tag_hybrid"] = debug
        results.append(payload)

    has_more = requested_offset + len(page_slice) < total_candidates
    _log.info(
        "performer_tag_hybrid: returning page",
        extra={
            "page_size": len(results),
            "total_candidates": total_candidates,
            "offset": requested_offset,
            "limit": requested_limit,
            "has_more": has_more,
            "preview": [
                {
                    "scene_id": payload.get("id"),
                    "score": payload.get("score"),
                    "sources": payload.get("debug_meta", {})
                    .get("performer_tag_hybrid", {})
                    .get("candidate_sources"),
                }
                for payload in results[:3]
            ],
        },
    )
    return {"scenes": results, "total": total_candidates, "has_more": has_more}
