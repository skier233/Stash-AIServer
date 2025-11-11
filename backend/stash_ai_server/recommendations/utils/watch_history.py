from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Set

import sqlalchemy as sa

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.interaction import SceneWatch, SceneWatchSegment


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def load_watch_history_summary(
    *,
    recent_cutoff: datetime | None = None,
    min_watch_seconds: float = 0.0,
    limit: int | None = None,
    order_desc: bool = True,
) -> List[Dict[str, Any]]:
    """Summarize scene watches aggregated across sessions.

    Each row in the returned list contains ``scene_id``, ``watched_s`` (sum of
    segment durations) and ``last_seen`` (UTC datetime). Results are ordered by
    most recent watch unless ``order_desc`` is ``False``.
    """
    stmt = (
        sa.select(
            SceneWatch.scene_id.label("scene_id"),
            sa.func.sum(SceneWatchSegment.watched_s).label("watched_s"),
            sa.func.max(SceneWatch.page_left_at).label("last_left"),
            sa.func.max(SceneWatch.page_entered_at).label("last_entered"),
            sa.func.max(SceneWatchSegment.created_at).label("last_segment"),
        )
        .join(SceneWatchSegment, SceneWatchSegment.scene_watch_id == SceneWatch.id)
        .group_by(SceneWatch.scene_id)
    )

    if recent_cutoff is not None:
        stmt = stmt.where(SceneWatch.page_entered_at >= recent_cutoff)
    if min_watch_seconds > 0:
        stmt = stmt.having(sa.func.sum(SceneWatchSegment.watched_s) >= min_watch_seconds)
    if order_desc:
        stmt = stmt.order_by(sa.func.max(SceneWatch.page_entered_at).desc())
    else:
        stmt = stmt.order_by(sa.func.max(SceneWatch.page_entered_at).asc())
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)

    rows: List[Dict[str, Any]] = []
    with SessionLocal() as session:
        for row in session.execute(stmt):
            watched_total = float(row.watched_s or 0.0)
            if watched_total <= 0:
                continue
            last_seen = row.last_left or row.last_entered or row.last_segment
            rows.append(
                {
                    "scene_id": int(row.scene_id),
                    "watched_s": watched_total,
                    "last_seen": _ensure_utc(last_seen),
                }
            )
    return rows


def load_recent_watch_scene_ids(
    *,
    recent_cutoff: datetime | None = None,
    min_watch_seconds: float = 0.0,
    limit: int | None = None,
) -> Set[int]:
    """Return a set of scene ids seen in the user's recent watch history."""
    summary = load_watch_history_summary(
        recent_cutoff=recent_cutoff,
        min_watch_seconds=min_watch_seconds,
        limit=limit,
    )
    return {entry["scene_id"] for entry in summary}
