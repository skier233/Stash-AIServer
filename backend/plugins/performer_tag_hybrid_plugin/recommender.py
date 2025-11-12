from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Dict, List, Set, Tuple

from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.recommendations.registry import recommender
from stash_ai_server.recommendations.utils.scene_fetch import (
    fetch_scene_candidates_by_performers,
    fetch_scenes_by_ids,
)
from stash_ai_server.recommendations.utils.tag_profiles import (
    build_watched_tag_profile,
    fetch_tag_document_frequencies,
    fetch_total_tagged_scene_count,
)
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


def _normalize_seed_values(payloads: Dict[int, Dict[str, Any]]) -> Tuple[Set[int], Set[int]]:
    """Collect performer and studio identifiers from the seed payloads."""
    performer_ids: Set[int] = set()
    studio_ids: Set[int] = set()
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
    return performer_ids, studio_ids


def _normalize_datetime(days: float) -> datetime | None:
    """Translate a floating day window into an absolute UTC cutoff."""
    if days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _build_tag_profile(
    *,
    service: str,
    history_summary: List[Dict[str, Any]],
    profile_limit: int,
) -> Dict[int, float]:
    """Aggregate tag durations from watch history into a capped preference profile."""

    if not history_summary:
        return {}

    # Maintain stable ordering of history scenes so the helper can process each watch
    # once; duplicates are filtered out.
    ordered_scene_ids: List[int] = []
    seen: Set[int] = set()

    for entry in history_summary:
        scene_id = entry.get("scene_id")
        if scene_id is None:
            continue
        try:
            scene_key = int(scene_id)
        except (TypeError, ValueError):
            continue
        if scene_key in seen:
            continue
        seen.add(scene_key)
        ordered_scene_ids.append(scene_key)

    # ``build_watched_tag_profile`` returns a tuple of
    # (aggregated_tag_seconds, total_watched_seconds, per_scene_breakdown). The breakdown
    # is not required here, but the aggregated map already prioritises watched segments
    # for these history scenes, giving us a consistent preference profile.
    aggregated, _, _ = build_watched_tag_profile(
        service=service,
        scene_ids=ordered_scene_ids,
        prefer_full_scene=False,
    )

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
    tag_interest_weights: Dict[int, float],
    tag_normalization_base: float,
    tag_weight: float,
    studio_ids: Set[int],
    studio_bonus: float,
) -> Tuple[float, Dict[str, Any]]:
    """Compute the blended performer/tag score for a single candidate scene."""

    performer_component = 0.0
    if seed_performers:
        unit = performer_weight / len(seed_performers)
        performer_component = unit * len(matched_performers)

    candidate_tags = tag_overlap_map.get(scene_id, {}) if tag_interest_weights else {}
    tag_component = 0.0
    tag_hits: List[Dict[str, Any]] = []
    weighted_sum = 0.0
    if tag_interest_weights and candidate_tags:
        normalization = tag_normalization_base or 1.0
        for tag_id, weight in tag_interest_weights.items():
            candidate_duration = float(candidate_tags.get(tag_id, 0.0))
            if candidate_duration <= 0:
                continue
            contribution = candidate_duration * weight
            weighted_sum += contribution
            tag_hits.append(
                {
                    "tag_id": tag_id,
                    "interest_weight": round(weight, 4),
                    "candidate_seconds": round(candidate_duration, 3),
                    "watched_seconds": round(tag_profile.get(tag_id, 0.0), 3),
                    "weighted_contribution": round(contribution, 4),
                }
            )
        if weighted_sum > 0:
            tag_component = tag_weight * min(1.0, weighted_sum / normalization)

    studio_component = 0.0
    studio = candidate_payload.get("studio") or {}
    studio_id = studio.get("id") if isinstance(studio, dict) else None
    if studio_id is not None and studio_ids and int(studio_id) in studio_ids:
        studio_component = studio_bonus

    score = performer_component + tag_component + studio_component
    debug = {
        "performer_component": round(performer_component, 4),
        "tag_component": round(tag_component, 4),
        "tag_weight_sum": round(weighted_sum, 4),
        "studio_bonus": round(studio_component, 4) if studio_component else 0.0,
        "performer_matches": sorted(int(pid) for pid in matched_performers),
        "tag_hits": tag_hits[:6],
    }
    return score, debug


@recommender(
    id="performer_tag_hybrid",
    label="Performer + Tag Hybrid",
    description="Blend performer overlap with historical tag preferences plus studio cues.",
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
    ],
    supports_pagination=True,
    exposes_scores=True,
    needs_seed_scenes=True,
    allows_multi_seed=True,
)
async def performer_tag_hybrid(ctx: Dict[str, Any], request: RecommendationRequest):
    """Blend performer overlap with historical tag preferences and optional studio bonuses."""

    cfg = request.config or {}

    # Step 1: extract seed ids and resolve configuration with guard rails.
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

    # Step 2: fetch seed payloads so we can extract performer/studio anchors for scoring.
    # ``fetch_scenes_by_ids`` returns a ``{scene_id: scene_payload}`` mapping containing
    # performer, studio, and other metadata used later in the scorer.
    seed_payloads = fetch_scenes_by_ids(seed_ids)
    if not seed_payloads:
        return {"scenes": [], "total": 0, "has_more": False}
    seed_performers, studio_ids = _normalize_seed_values(seed_payloads)

    # Step 3: load recent watch history to anchor preferences.
    recent_cutoff = _normalize_datetime(recent_days)

    # ``load_watch_history_summary`` yields a list of dict entries with fields such as
    # ``scene_id``, ``watched_s``, and ``last_seen`` ordered from most to least recent.
    history_summary = load_watch_history_summary(
        recent_cutoff=recent_cutoff,
        min_watch_seconds=min_watch_seconds,
        limit=history_limit,
    )
    watched_scene_ids = {entry["scene_id"] for entry in history_summary}
    exclude_set = watched_scene_ids | set(seed_ids)

    # Step 4: turn the history into a tag preference profile and derive TF-IDF weights.
    tag_profile = (
        _build_tag_profile(
            service=service_name,
            history_summary=history_summary,
            profile_limit=tag_profile_limit,
        )
        if tag_weight > 0
        else {}
    )

    tag_interest_weights: Dict[int, float] = {}
    tag_normalization_base = 1.0
    if tag_profile:
        profile_total = sum(tag_profile.values())
        if profile_total > 0:
            base_weights = {tag_id: value / profile_total for tag_id, value in tag_profile.items() if value > 0}
        else:
            base_weights = {}

        if base_weights:
            doc_frequencies = fetch_tag_document_frequencies(service=service_name, tag_ids=base_weights.keys())
            total_tagged_scenes = fetch_total_tagged_scene_count(service=service_name) or len(doc_frequencies) or 1

            weighted: Dict[int, float] = {}
            for tag_id, tf_weight in base_weights.items():
                df = max(0, doc_frequencies.get(tag_id, 0))
                idf = math.log((1 + total_tagged_scenes) / (1 + df)) + 1.0
                weighted[tag_id] = tf_weight * idf

            weight_sum = sum(weighted.values())
            if weight_sum > 0:
                tag_interest_weights = {tag_id: value / weight_sum for tag_id, value in weighted.items() if value > 0}
            else:
                tag_interest_weights = base_weights

            tag_normalization_base = 0.0
            for tag_id, weight in tag_interest_weights.items():
                tag_normalization_base += weight * tag_profile.get(tag_id, 0.0)
            if tag_normalization_base <= 0:
                tag_normalization_base = 1.0

    # Step 5: assemble a pool of candidates sourced from performers and tag overlap.
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
        # ``fetch_scene_candidates_by_performers`` returns a list of tuples where the
        # first element is the candidate scene id and the second is the set of performer
        # ids shared with the seed set.
        performer_candidates = fetch_scene_candidates_by_performers(
            performer_ids=list(seed_performers),
            exclude_scene_ids=exclude_set,
            limit=candidate_pool * 2,
        )
        for scene_id, matched in performer_candidates:
            add_candidate(scene_id, matched, "performer")

    tag_duration_index: Dict[int, Dict[int, float]] = {}
    if tag_interest_weights and tag_weight > 0:
        # ``collect_tag_durations`` yields ``{scene_id: {tag_id: duration_seconds}}`` for
        # the requested tag ids. This lets us compute weighted relevance against the
        # active tag profile ahead of the detailed scoring pass.
        tag_duration_index = collect_tag_durations(service=service_name, tag_ids=tag_interest_weights.keys())
        tag_strength_items: List[Tuple[int, float]] = []
        for scene_id, tag_map in tag_duration_index.items():
            if scene_id in exclude_set:
                continue
            weighted_sum = 0.0
            for tag_id, weight in tag_interest_weights.items():
                candidate_duration = float(tag_map.get(tag_id, 0.0))
                if candidate_duration <= 0:
                    continue
                weighted_sum += candidate_duration * weight
            if weighted_sum <= 0:
                continue
            normalized_strength = weighted_sum / tag_normalization_base if tag_normalization_base > 0 else weighted_sum
            tag_strength_items.append((scene_id, normalized_strength))
        tag_strength_items.sort(key=lambda item: item[1], reverse=True)
        for scene_id, normalized_strength in tag_strength_items:
            add_candidate(scene_id, None, "tag", tag_strength=normalized_strength)
            if max_pre_score is not None and len(candidate_scene_ids) >= max_pre_score:
                break

    if not candidate_scene_ids:
        return {"scenes": [], "total": 0, "has_more": False}

    for scene_id in candidate_scene_ids:
        matched_performer_map.setdefault(scene_id, set())
        candidate_origin.setdefault(scene_id, {"unknown"})

    candidate_tag_map = (
        {scene_id: tag_duration_index.get(scene_id, {}) for scene_id in candidate_scene_ids}
        if tag_interest_weights and tag_weight > 0
        else {}
    )

    # Step 6: score candidates using the blended performer/tag model.
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
            tag_interest_weights=tag_interest_weights,
            tag_normalization_base=tag_normalization_base,
            tag_weight=tag_weight,
            studio_ids=studio_ids,
            studio_bonus=studio_bonus,
        )
        if score <= 0:
            continue
        scored_candidates.append((scene_id, score, debug))

    if not scored_candidates:
        return {"scenes": [], "total": 0, "has_more": False}

    scored_candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
    if len(scored_candidates) > candidate_pool:
        scored_candidates = scored_candidates[:candidate_pool]

    # Step 7: paginate and project debug metadata for inspection.
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
    return {"scenes": results, "total": total_candidates, "has_more": has_more}
