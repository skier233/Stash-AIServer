from __future__ import annotations

from collections import defaultdict
from typing import Dict, Mapping, Sequence, Tuple

import sqlalchemy as sa

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.ai_results import AIModelRun, AIResultAggregate


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
