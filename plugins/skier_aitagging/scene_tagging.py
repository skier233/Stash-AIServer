from __future__ import annotations

import logging
from typing import Sequence

from stash_ai_server.db.ai_results_store import get_scene_tag_totals_async
from stash_ai_server.utils.stash_api import stash_api
from stash_ai_server.core.system_settings import get_value as sys_get_value

from .stash_handler import AI_tags_cache
from .tag_config import SceneTagDurationRequirement, TagSettings, get_tag_configuration
from . import stash_handler

_log = logging.getLogger(__name__)



#TODO: look at the logic of passing in existing scene tags as its kinda weird
async def apply_scene_tags(
    *,
    scene_id: int,
    service_name: str,
    scene_duration: float,
    existing_scene_tag_ids: Sequence[int] | None = None,
    apply_ai_tagged_tag: bool = True,
) -> dict[str, list[int]]:
    """Apply scene-level AI tags based on stored aggregates.

    Returns a dict with ``applied`` and ``removed`` keys listing tag ids.
    """

    config = get_tag_configuration()

    # Get excluded tags from system settings
    excluded_tags_raw = sys_get_value('EXCLUDED_TAGS', [])
    excluded_tag_names = []
    if excluded_tags_raw is not None:
        if isinstance(excluded_tags_raw, str):
            import json
            try:
                excluded_tags_raw = json.loads(excluded_tags_raw)
            except:
                excluded_tags_raw = []
        if isinstance(excluded_tags_raw, list):
            excluded_tag_names = [str(tag).strip() for tag in excluded_tags_raw if tag]
    
    _log.info(
        "apply_scene_tags: Retrieved excluded_tag_names from system settings: %s (count=%d) for scene_id=%s",
        excluded_tag_names,
        len(excluded_tag_names),
        scene_id
    )

    try:
        totals = await get_scene_tag_totals_async(
            service=service_name,
            scene_id=scene_id,
        )
    except Exception:
        _log.exception("Failed to load scene tag aggregates for scene_id=%s", scene_id)
        totals = {}

    aggregate_totals = {int(tag_id): float(duration or 0.0) for tag_id, duration in (totals or {}).items()}
    
    _log.info(
        "apply_scene_tags: Retrieved tag totals for scene_id=%s: %d tags before filtering",
        scene_id,
        len(aggregate_totals)
    )
    
    # Filter out excluded tags from aggregate_totals
    excluded_tag_ids = set()
    if excluded_tag_names:
        for tag_id in list(aggregate_totals.keys()):
            tag_name = stash_api.get_stash_tag_name(tag_id)
            if tag_name and tag_name in excluded_tag_names:
                excluded_tag_ids.add(tag_id)
                _log.debug(
                    "apply_scene_tags: Excluding tag_id=%d (name='%s') from scene_id=%s",
                    tag_id,
                    tag_name,
                    scene_id
                )
    
    if excluded_tag_ids:
        _log.info(
            "apply_scene_tags: Filtering out %d excluded tags (tag_ids: %s) from scene_id=%s",
            len(excluded_tag_ids),
            sorted(excluded_tag_ids),
            scene_id
        )
        # Remove excluded tags from aggregate_totals
        for tag_id in excluded_tag_ids:
            aggregate_totals.pop(tag_id, None)
    
    _log.info(
        "apply_scene_tags: Tag totals after filtering excluded tags: %d tags for scene_id=%s",
        len(aggregate_totals),
        scene_id
    )

    current_ai_tags = {
        int(tag_id)
        for tag_id in (existing_scene_tag_ids or [])
        if isinstance(tag_id, int)
    }

    tags_to_add: set[int] = set()
    tags_to_remove: set[int] = set()

    async def _evaluate_tag(tag_id: int, duration: float) -> None:
        tag_name = stash_api.get_stash_tag_name(tag_id)
        if not tag_name:
            return

        if tag_name not in AI_tags_cache:
            AI_tags_cache[tag_name] = tag_id

        settings = config.resolve(tag_name)
        threshold = settings.required_scene_tag_duration.as_seconds(scene_duration)
        if threshold is None:
            _log.warning(
                "Skipping percentage-based scene tag '%s' for scene_id=%s due to missing duration",
                tag_name,
                scene_id,
            )
            return

        if not settings.scene_tag_enabled:
            tags_to_remove.add(tag_id)
            return

        if duration >= threshold:
            tags_to_add.add(tag_id)
        else:
            tags_to_remove.add(tag_id)

    for tag_id, duration in aggregate_totals.items():
        await _evaluate_tag(tag_id, duration)

    # Remove anything we manage that is currently on the scene and in our ai tag cache
    for tag_id in current_ai_tags:
        if tag_id not in tags_to_add and tag_id in AI_tags_cache.values():
            tags_to_remove.add(tag_id)

    if apply_ai_tagged_tag and stash_handler.AI_Tagged_Tag_Id:
        tags_to_add.add(stash_handler.AI_Tagged_Tag_Id)
    # Avoid removing tags we plan to add again.
    tags_to_remove.difference_update(tags_to_add)

    removed_ids = list(tags_to_remove) if tags_to_remove else []
    applied_ids = list(tags_to_add) if tags_to_add else []
    
    _log.info(
        "apply_scene_tags: Final tag counts for scene_id=%s: applied=%d tags (tag_ids: %s), removed=%d tags (tag_ids: %s)",
        scene_id,
        len(applied_ids),
        sorted(applied_ids),
        len(removed_ids),
        sorted(removed_ids)
    )

    if removed_ids:
        try:
            await stash_api.remove_tags_from_scene_async(scene_id, removed_ids)
        except Exception:
            _log.exception("Failed to remove scene tags %s from scene_id=%s", removed_ids, scene_id)
    if applied_ids:
        try:
            await stash_api.add_tags_to_scene_async(scene_id, applied_ids)
        except Exception:
            _log.exception("Failed to apply scene tags %s to scene_id=%s", applied_ids, scene_id)

    return {"applied": applied_ids, "removed": removed_ids}