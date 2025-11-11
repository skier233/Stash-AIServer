from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple

import sqlalchemy as sa

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.ai_results import AIModelRun, AIResultAggregate
from stash_ai_server.db.ai_results_store import get_scene_timespans
from stash_ai_server.models.interaction import SceneWatchSegment


TagDurationMap = Dict[int, Dict[int, float]]
Interval = Tuple[float, float]


def collect_tag_durations(
    *,
    service: str,
    tag_ids: Iterable[int],
    scene_ids: Iterable[int] | None = None,
) -> TagDurationMap:
    """Return accumulated duration (seconds) per scene/tag for the provided tag ids.

    Only rows whose metric is ``duration_s`` and payload type ``tag`` are considered. Results
    are aggregated across all runs for the requested service.
    """
    tag_set = {int(tag_id) for tag_id in tag_ids if tag_id is not None}
    if not tag_set:
        return {}

    scene_set = {int(scene_id) for scene_id in scene_ids or [] if scene_id is not None}

    with SessionLocal() as session:
        stmt = (
            sa.select(
                AIModelRun.entity_id.label("scene_id"),
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
                AIResultAggregate.value_id.in_(tag_set),
            )
            .group_by(AIModelRun.entity_id, AIResultAggregate.value_id)
        )
        if scene_set:
            stmt = stmt.where(AIModelRun.entity_id.in_(scene_set))

        durations: TagDurationMap = defaultdict(dict)
        for scene_id, tag_id, duration in session.execute(stmt):
            scene_int = int(scene_id)
            tag_int = int(tag_id)
            durations[scene_int][tag_int] = durations[scene_int].get(tag_int, 0.0) + float(duration or 0.0)

    return dict(durations)


def merge_intervals(intervals: Sequence[Interval]) -> List[Interval]:
    """Merge overlapping or adjacent time intervals."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: List[Interval] = []
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
            continue
        merged.append((cur_start, cur_end))
        cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    return merged


def intersect_two(a: Sequence[Interval], b: Sequence[Interval]) -> List[Interval]:
    """Return pairwise intersection of two interval lists."""
    i = j = 0
    intersections: List[Interval] = []
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if end > start:
            intersections.append((start, end))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return intersections


def intersect_all(interval_groups: Sequence[Sequence[Interval]]) -> List[Interval]:
    """Return the set of intervals where **all** provided interval lists overlap."""
    if not interval_groups:
        return []
    current = merge_intervals(interval_groups[0])
    for group in interval_groups[1:]:
        merged = merge_intervals(group)
        current = intersect_two(current, merged)
        if not current:
            break
    return current


def compute_cooccurrence_duration(
    *,
    service: str,
    scene_id: int,
    tag_ids: Iterable[int],
) -> float:
    """Compute the total seconds where all provided tags overlap in the scene timespans."""
    tag_list = [int(tag_id) for tag_id in tag_ids if tag_id is not None]
    if not tag_list:
        return 0.0

    bucket_map = get_scene_timespans(service=service, scene_id=int(scene_id))
    if not bucket_map:
        return 0.0

    interval_groups: List[List[Interval]] = []
    for tag_id in tag_list:
        target_key = str(tag_id)
        intervals: List[Interval] = []
        for category_map in bucket_map.values():
            tag_entries = category_map.get(target_key)
            if not tag_entries:
                continue
            for entry in tag_entries:
                start = float(entry.get("start") or 0.0)
                end_val = entry.get("end")
                end = float(end_val) if end_val is not None else start
                if end <= start:
                    continue
                intervals.append((start, end))
        if not intervals:
            return 0.0
        interval_groups.append(intervals)

    overlap = intersect_all(interval_groups)
    return sum(max(0.0, end - start) for start, end in overlap)


def _fetch_scene_watch_intervals(scene_id: int) -> List[Interval]:
    """Load and merge watch intervals for the requested scene."""
    stmt = (
        sa.select(
            SceneWatchSegment.start_s,
            SceneWatchSegment.end_s,
            SceneWatchSegment.watched_s,
        )
        .where(SceneWatchSegment.scene_id == int(scene_id))
        .order_by(SceneWatchSegment.start_s.asc())
    )
    intervals: List[Interval] = []
    with SessionLocal() as session:
        for start, end, watched in session.execute(stmt):
            try:
                start_f = float(start or 0.0)
            except (TypeError, ValueError):
                continue
            try:
                end_f = float(end or 0.0)
            except (TypeError, ValueError):
                end_f = start_f
            if end_f <= start_f:
                try:
                    watched_val = float(watched or 0.0)
                except (TypeError, ValueError):
                    watched_val = 0.0
                if watched_val > 0:
                    end_f = start_f + watched_val
            if end_f <= start_f:
                continue
            intervals.append((start_f, end_f))
    return merge_intervals(intervals)


def collect_watched_segment_tag_durations(
    *,
    service: str,
    scene_id: int,
    min_confidence: float | None = None,
) -> Tuple[Dict[int, float], float]:
    """Compute tag coverage limited to segments actually watched for a scene.

    Returns a tuple ``(tag_duration_map, watched_total)`` where
    ``tag_duration_map`` maps tag ids to the number of seconds that overlap the
    user's merged watch segments and ``watched_total`` is the total seconds
    covered by those merged watch segments. When no overlap exists the
    dictionary is empty and ``watched_total`` is zero.
    """

    watch_intervals = _fetch_scene_watch_intervals(scene_id)
    if not watch_intervals:
        return {}, 0.0

    total_watched = sum(max(0.0, end - start) for start, end in watch_intervals)
    bucket_map = get_scene_timespans(service=service, scene_id=int(scene_id))
    if not bucket_map:
        return {}, total_watched
    min_conf = None
    if min_confidence is not None:
        try:
            min_conf = float(min_confidence)
        except (TypeError, ValueError):
            min_conf = None

    tag_durations: Dict[int, float] = {}

    for category_map in bucket_map.values():
        for tag_key, entries in category_map.items():
            try:
                tag_id = int(tag_key)
            except (TypeError, ValueError):
                continue
            if not entries:
                continue
            tag_intervals: List[Interval] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                try:
                    start = float(entry.get("start") or 0.0)
                except (TypeError, ValueError):
                    continue
                end_value = entry.get("end")
                try:
                    end = float(end_value) if end_value is not None else start
                except (TypeError, ValueError):
                    continue
                if end <= start:
                    continue
                if min_conf is not None:
                    confidence_raw = entry.get("confidence")
                    try:
                        confidence_val = float(confidence_raw)
                    except (TypeError, ValueError):
                        confidence_val = None
                    if confidence_val is None or confidence_val < min_conf:
                        continue
                tag_intervals.append((start, end))
            if not tag_intervals:
                continue
            merged_tag = merge_intervals(tag_intervals)
            overlap_intervals = intersect_two(merged_tag, watch_intervals)
            overlap_duration = sum(max(0.0, interval_end - interval_start) for interval_start, interval_end in overlap_intervals)
            if overlap_duration <= 0:
                continue
            tag_durations[tag_id] = tag_durations.get(tag_id, 0.0) + overlap_duration

    return tag_durations, total_watched
