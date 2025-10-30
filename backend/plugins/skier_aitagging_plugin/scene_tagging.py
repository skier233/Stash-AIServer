from __future__ import annotations

import logging
from typing import Sequence

from stash_ai_server.db.ai_results_store import get_scene_tag_totals_async
from stash_ai_server.utils.stash_api import stash_api

from .stash_handler import AI_tags_cache
from .tag_config import TagSettings, get_tag_configuration

_log = logging.getLogger(__name__)


def _required_duration(settings: TagSettings) -> float:
    threshold = settings.required_scene_tag_duration
    return float(threshold) if threshold is not None else 0.0

#TODO: look at the logic of passing in existing scene tags as its kinda weird
async def apply_scene_tags(
    *,
    scene_id: int,
    service_name: str,
    existing_scene_tag_ids: Sequence[int] | None = None,
) -> dict[str, list[int]]:
    """Apply scene-level AI tags based on stored aggregates.

    Returns a dict with ``applied`` and ``removed`` keys listing tag ids.
    """

    config = get_tag_configuration()

    try:
        totals = await get_scene_tag_totals_async(
            service=service_name,
            scene_id=scene_id,
        )
    except Exception:
        _log.exception("Failed to load scene tag aggregates for scene_id=%s", scene_id)
        totals = {}

    aggregate_totals = {int(tag_id): float(duration or 0.0) for tag_id, duration in (totals or {}).items()}

    current_ai_tags = {
        int(tag_id)
        for tag_id in (existing_scene_tag_ids or [])
        if isinstance(tag_id, int)
    }

    tags_to_add: set[int] = set()
    tags_to_remove: set[int] = set()

    def _evaluate_tag(tag_id: int, duration: float) -> None:
        tag_name = stash_api.get_stash_tag_name(tag_id)
        if not tag_name:
            return

        if tag_name not in AI_tags_cache:
            AI_tags_cache[tag_name] = tag_id

        settings = config.resolve(tag_name)
        threshold = _required_duration(settings)

        if not settings.scene_tag_enabled:
            tags_to_remove.add(tag_id)
            return

        if duration >= threshold:
            tags_to_add.add(tag_id)
        else:
            tags_to_remove.add(tag_id)

    for tag_id, duration in aggregate_totals.items():
        _evaluate_tag(tag_id, duration)

    # Remove anything we manage that is currently on the scene and in our ai tag cache
    for tag_id in current_ai_tags:
        if tag_id not in tags_to_add and tag_id in AI_tags_cache.values():
            tags_to_remove.add(tag_id)

    # Avoid removing tags we plan to add again.
    tags_to_remove.difference_update(tags_to_add)

    applied = tags_to_add
    removed = tags_to_remove

    removed_ids = list(removed) if removed else []
    applied_ids = list(applied) if applied else []

    if removed_ids:
        try:
            stash_api.remove_tags_from_scene(scene_id, removed_ids)
        except Exception:
            _log.exception("Failed to remove scene tags %s from scene_id=%s", removed_ids, scene_id)
    if applied_ids:
        try:
            stash_api.add_tags_to_scene(scene_id, applied_ids)
        except Exception:
            _log.exception("Failed to apply scene tags %s to scene_id=%s", applied_ids, scene_id)

    return {"applied": applied_ids, "removed": removed_ids}