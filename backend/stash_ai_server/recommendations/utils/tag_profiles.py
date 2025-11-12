from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Mapping, Sequence, Tuple

import sqlalchemy as sa

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.ai_results import AIModelRun, AIResultAggregate
from stash_ai_server.db.ai_results_store import get_scene_tag_totals

from .timespan_metrics import collect_watched_segment_tag_durations


TagDurationLookup = Dict[int, Dict[int, float]]


def fetch_tag_durations_for_scenes(
    *,
    service: str,
    scene_ids: Sequence[int],
) -> Tuple[TagDurationLookup, set[int]]:
    """Return per-scene tag duration totals for the provided scene ids.

    The result is a tuple of ``(per_scene_map, tag_ids)`` where ``per_scene_map``
    maps each scene id to a ``{tag_id: duration_seconds}`` dictionary and
    ``tag_ids`` is the union of all tag identifiers encountered. Scenes without
    any recorded tag durations are omitted from the map.
    """
    normalized_ids = [int(scene_id) for scene_id in scene_ids if scene_id is not None]
    if not normalized_ids:
        return {}, set()

    stmt = (
        sa.select(
            AIResultAggregate.entity_id.label("scene_id"),
            AIResultAggregate.value_id.label("tag_id"),
            sa.func.sum(AIResultAggregate.value_float).label("duration_s"),
        )
        .join(AIModelRun, AIResultAggregate.run_id == AIModelRun.id)
        .where(
            AIModelRun.service == service,
            AIModelRun.entity_type == "scene",
            AIResultAggregate.payload_type == "tag",
            AIResultAggregate.metric == "duration_s",
            AIResultAggregate.value_id.isnot(None),
            AIResultAggregate.entity_id.in_(normalized_ids),
        )
        .group_by(AIResultAggregate.entity_id, AIResultAggregate.value_id)
    )

    per_scene: TagDurationLookup = defaultdict(dict)
    tag_ids: set[int] = set()

    with SessionLocal() as session:
        for row in session.execute(stmt):
            tag = row.tag_id
            if tag is None:
                continue
            try:
                scene_id = int(row.scene_id)
                tag_id = int(tag)
                duration_val = float(row.duration_s or 0.0)
            except (TypeError, ValueError):
                continue
            if duration_val <= 0:
                continue
            per_scene[scene_id][tag_id] = duration_val
            tag_ids.add(tag_id)

    return dict(per_scene), tag_ids


def _accumulate_tag_durations(target: Dict[int, float], source: Mapping[int, float]) -> None:
    """Add tag durations from ``source`` into ``target`` after basic validation."""

    if not source:
        return

    for tag_id, duration in source.items():
        try:
            duration_val = float(duration)
        except (TypeError, ValueError):
            continue
        if duration_val <= 0:
            continue
        target[tag_id] = target.get(tag_id, 0.0) + duration_val


def build_watched_tag_profile(
    *,
    service: str,
    scene_ids: Sequence[int],
    prefer_full_scene: bool = False,
    min_confidence: float | None = None,
) -> Tuple[Dict[int, float], float, Dict[int, Dict[str, Any]]]:
    """Aggregate tag coverage for the provided scenes using watched segments where available.

    Returns a tuple ``(aggregated_tags, total_watched, scene_breakdown)`` where

    * ``aggregated_tags`` maps tag identifiers to accumulated seconds.
    * ``total_watched`` is the sum of watched seconds across all scenes.
    * ``scene_breakdown`` captures per-scene watch and fallback details for debugging.

    When ``prefer_full_scene`` is true the aggregation favours full-scene tag totals,
    only reverting to watched segments if no fallback data exists. Otherwise only watched
    segment coverage contributes to ``aggregated_tags``.
    """

    aggregated: Dict[int, float] = {}
    total_watched = 0.0
    breakdown: Dict[int, Dict[str, Any]] = {}

    for raw_scene_id in scene_ids:
        try:
            scene_id = int(raw_scene_id)
        except (TypeError, ValueError):
            continue

        detail = breakdown.setdefault(
            scene_id,
            {
                "watch_seconds": 0.0,
                "watch_tags": {},
                "fallback_tags": {},
            },
        )

        watch_map_raw, watched_total = collect_watched_segment_tag_durations(
            service=service,
            scene_id=scene_id,
            min_confidence=min_confidence,
        )
        total_watched += watched_total
        detail["watch_seconds"] = float(watched_total or 0.0)

        watch_map: Dict[int, float] = {}
        for tag_id, duration in (watch_map_raw or {}).items():
            try:
                tag_key = int(tag_id)
                duration_val = float(duration)
            except (TypeError, ValueError):
                continue
            if duration_val <= 0:
                continue
            watch_map[tag_key] = duration_val
        detail["watch_tags"] = watch_map

        fallback_tags: Dict[int, float] = {}
        if prefer_full_scene:
            fallback_map = get_scene_tag_totals(service=service, scene_id=scene_id)
            for tag_id, duration in (fallback_map or {}).items():
                try:
                    tag_key = int(tag_id)
                    duration_val = float(duration)
                except (TypeError, ValueError):
                    continue
                if duration_val <= 0:
                    continue
                fallback_tags[tag_key] = duration_val
        detail["fallback_tags"] = fallback_tags

        if prefer_full_scene:
            if fallback_tags:
                _accumulate_tag_durations(aggregated, fallback_tags)
            elif watch_map:
                _accumulate_tag_durations(aggregated, watch_map)
            continue

        if watch_map:
            _accumulate_tag_durations(aggregated, watch_map)

    return aggregated, total_watched, breakdown


def fetch_tag_document_frequencies(
    *,
    service: str,
    tag_ids: Sequence[int],
) -> Dict[int, int]:
    """Return how many distinct scenes each tag appears in for the service."""

    normalized_ids = [int(tag_id) for tag_id in tag_ids if tag_id is not None]
    if not normalized_ids:
        return {}

    stmt = (
        sa.select(
            AIResultAggregate.value_id.label("tag_id"),
            sa.func.count(sa.distinct(AIModelRun.entity_id)).label("scene_count"),
        )
        .join(AIModelRun, AIResultAggregate.run_id == AIModelRun.id)
        .where(
            AIModelRun.service == service,
            AIModelRun.entity_type == "scene",
            AIResultAggregate.payload_type == "tag",
            AIResultAggregate.metric == "duration_s",
            AIResultAggregate.value_id.isnot(None),
            AIResultAggregate.value_id.in_(normalized_ids),
        )
        .group_by(AIResultAggregate.value_id)
    )

    frequencies: Dict[int, int] = {}
    with SessionLocal() as session:
        for row in session.execute(stmt):
            try:
                tag_id = int(row.tag_id)
                count = int(row.scene_count)
            except (TypeError, ValueError):
                continue
            frequencies[tag_id] = count
    return frequencies


def fetch_total_tagged_scene_count(*, service: str) -> int:
    """Return the total number of scenes with tag duration data for the service."""

    stmt = (
        sa.select(sa.func.count(sa.distinct(AIModelRun.entity_id)))
        .join(AIResultAggregate, AIResultAggregate.run_id == AIModelRun.id)
        .where(
            AIModelRun.service == service,
            AIModelRun.entity_type == "scene",
            AIResultAggregate.payload_type == "tag",
            AIResultAggregate.metric == "duration_s",
            AIResultAggregate.value_id.isnot(None),
        )
    )

    with SessionLocal() as session:
        result = session.execute(stmt).scalar_one_or_none()
        try:
            return int(result or 0)
        except (TypeError, ValueError):
            return 0
