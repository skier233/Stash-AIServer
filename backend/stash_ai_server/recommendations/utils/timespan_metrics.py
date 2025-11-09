from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple

import sqlalchemy as sa

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.ai_results import AIModelRun, AIResultAggregate
from stash_ai_server.db.ai_results_store import get_scene_timespans


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

    timespan_data = get_scene_timespans(service=service, scene_id=int(scene_id))
    if not timespan_data:
        return 0.0
    _, bucket_map = timespan_data

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
