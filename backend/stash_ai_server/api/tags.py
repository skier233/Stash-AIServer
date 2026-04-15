"""Tags utility API — expose Stash tag names for frontend autocomplete."""
from __future__ import annotations

import logging
from typing import List, Set

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from stash_ai_server.core.api_key import require_shared_api_key
from stash_ai_server.utils import stash_db

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/tags",
    tags=["tags"],
    dependencies=[Depends(require_shared_api_key)],
)


@router.get("/names")
async def list_tag_names():
    """Return all Stash tag names sorted alphabetically."""
    tags: List[str] = []
    try:
        session_factory = stash_db.get_stash_sessionmaker()
        tags_table = stash_db.get_stash_table("tags", required=False)
        if session_factory is None or tags_table is None:
            return {"tags": []}
        name_col = tags_table.c.get("name")
        if name_col is None:
            return {"tags": []}
        with session_factory() as session:
            rows = session.execute(
                sa.select(name_col).order_by(name_col.asc())
            ).scalars().all()
            tags = [str(r) for r in rows]
    except Exception:
        _log.debug("Could not load tag names", exc_info=True)
    return {"tags": tags}


def resolve_and_filter_scene_ids(
    scene_ids: List[int],
    *,
    exclude_tag_names: List[str] | None = None,
    include_tag_names: List[str] | None = None,
) -> List[int]:
    """Filter a list of scene IDs by tag inclusion/exclusion.

    Returns the subset of scene_ids that match the tag constraints.
    Used as a shared post-filter for recommendations and training batches.
    """
    if not scene_ids:
        return []
    if not exclude_tag_names and not include_tag_names:
        return scene_ids

    from stash_ai_server.recommendations.utils.stash_tags import (
        fetch_scene_tag_ids,
        resolve_tag_ids_by_name,
    )

    # Resolve tag names to IDs
    all_names = list(set((exclude_tag_names or []) + (include_tag_names or [])))
    name_to_id = resolve_tag_ids_by_name(all_names)

    exclude_ids: Set[int] = set()
    include_ids: Set[int] = set()
    if exclude_tag_names:
        exclude_ids = {name_to_id[n] for n in exclude_tag_names if n in name_to_id}
    if include_tag_names:
        include_ids = {name_to_id[n] for n in include_tag_names if n in name_to_id}

    if not exclude_ids and not include_ids:
        return scene_ids

    # Fetch tags for all scenes
    scene_tags = fetch_scene_tag_ids(scene_ids)

    result: List[int] = []
    for sid in scene_ids:
        tags = scene_tags.get(sid, set())
        # Exclusion: skip scenes that have ANY excluded tag
        if exclude_ids and tags & exclude_ids:
            continue
        # Inclusion: keep only scenes that have at least one included tag
        if include_ids and not (tags & include_ids):
            continue
        result.append(sid)

    return result
