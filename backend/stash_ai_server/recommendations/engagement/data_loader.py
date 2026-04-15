"""Batch data loading for engagement scoring.

Queries both the Stash SQLite database (read-only) and the AI Server
PostgreSQL database to gather all available engagement signals for a set of
scene (or image) IDs in as few queries as possible.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Sequence

import sqlalchemy as sa

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.interaction import (
    SceneDerived,
    SceneWatch,
    SceneWatchSegment,
)
from stash_ai_server.models.ratings import EntityRating
from stash_ai_server.utils import stash_db

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type alias for the per-scene raw data bundle
# ---------------------------------------------------------------------------

RawSignalBundle = dict[str, Any]
"""Keys match signal names in signals.py plus context keys like scene_duration."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, (int, float)):
        try:
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
        except Exception:
            return None
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _days_ago(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    now = _now_utc()
    # Ensure both are timezone-aware before subtracting
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    return max(0.0, delta.total_seconds() / 86400.0)


def _pick_col(table: sa.Table | None, *names: str) -> sa.ColumnElement | None:
    if table is None:
        return None
    for n in names:
        c = table.c.get(n)
        if c is not None:
            return c
    return None


# ---------------------------------------------------------------------------
# Stash DB loader
# ---------------------------------------------------------------------------

def _load_stash_data(scene_ids: Sequence[int]) -> dict[int, RawSignalBundle]:
    """Load engagement-relevant columns from the Stash SQLite database.

    Returns a dict keyed by scene_id with available raw values.
    Gracefully returns empty if Stash DB is unavailable.
    """
    if not scene_ids:
        return {}

    session_factory = stash_db.get_stash_sessionmaker()
    scenes_table = stash_db.get_stash_table("scenes", required=False)
    if session_factory is None or scenes_table is None:
        return {}

    id_col = scenes_table.c.get("id")
    if id_col is None:
        return {}

    # Discover available columns
    rating_col = _pick_col(scenes_table, "rating")
    o_counter_col = _pick_col(scenes_table, "o_counter")
    play_count_col = _pick_col(scenes_table, "play_count")
    play_duration_col = _pick_col(scenes_table, "play_duration")
    resume_time_col = _pick_col(scenes_table, "resume_time")
    last_played_col = _pick_col(scenes_table, "last_played_at")
    duration_col = _pick_col(
        scenes_table,
        "duration", "duration_s", "duration_seconds",
    )

    # Build SELECT with only available columns
    columns: list[sa.ColumnElement] = [id_col.label("id")]

    def _add(col: sa.ColumnElement | None, alias: str) -> None:
        if col is not None:
            columns.append(col.label(alias))
        else:
            columns.append(sa.literal(None).label(alias))

    _add(rating_col, "rating")
    _add(o_counter_col, "o_counter")
    _add(play_count_col, "play_count")
    _add(play_duration_col, "play_duration")
    _add(resume_time_col, "resume_time")
    _add(last_played_col, "last_played_at")
    _add(duration_col, "duration")

    results: dict[int, RawSignalBundle] = {}
    normalized_ids = [int(sid) for sid in scene_ids if sid is not None]
    if not normalized_ids:
        return results

    try:
        with session_factory() as session:
            stmt = sa.select(*columns).where(id_col.in_(normalized_ids))
            for row in session.execute(stmt):
                m = row._mapping
                try:
                    scene_id = int(m["id"])
                except Exception:
                    continue

                # Parse rating → rating100 convention
                raw_rating = m.get("rating")
                rating100 = None
                if raw_rating is not None:
                    try:
                        rating100 = int(round(float(raw_rating)))
                    except Exception:
                        pass

                # Parse duration
                duration_val = None
                raw_dur = m.get("duration")
                if raw_dur is not None:
                    try:
                        duration_val = float(raw_dur)
                        if duration_val <= 0:
                            duration_val = None
                    except Exception:
                        pass

                # Parse o_counter
                o_counter = None
                raw_oc = m.get("o_counter")
                if raw_oc is not None:
                    try:
                        o_counter = int(raw_oc)
                    except Exception:
                        pass

                # Parse play_count
                raw_pc = m.get("play_count")
                play_count = None
                if raw_pc is not None:
                    try:
                        play_count = int(raw_pc)
                    except Exception:
                        pass

                # Parse play_duration
                raw_pd = m.get("play_duration")
                play_duration = None
                if raw_pd is not None:
                    try:
                        play_duration = float(raw_pd)
                        if play_duration <= 0:
                            play_duration = None
                    except Exception:
                        pass

                # Parse resume_time
                raw_rt = m.get("resume_time")
                resume_time = None
                if raw_rt is not None:
                    try:
                        resume_time = float(raw_rt)
                        if resume_time <= 0:
                            resume_time = None
                    except Exception:
                        pass

                # Parse last_played_at
                last_played_dt = _parse_datetime(m.get("last_played_at"))

                results[scene_id] = {
                    "stash_rating": rating100,
                    "stash_o_counter": o_counter,
                    "stash_play_count": play_count,
                    "stash_play_duration": play_duration,
                    "stash_resume_time": resume_time,
                    "stash_last_played_at": last_played_dt,
                    "stash_duration": duration_val,
                }
    except Exception:
        _log.exception("Failed to load Stash DB data for engagement scoring")

    return results


# ---------------------------------------------------------------------------
# AI Server DB loader
# ---------------------------------------------------------------------------

def _load_ai_server_data(
    scene_ids: Sequence[int],
    entity_type: str = "scene",
) -> dict[int, RawSignalBundle]:
    """Load engagement data from the AI Server PostgreSQL database.

    Fetches SceneDerived, EntityRating, SceneWatch aggregates.
    """
    if not scene_ids:
        return {}

    normalized_ids = [int(sid) for sid in scene_ids if sid is not None]
    if not normalized_ids:
        return {}

    results: dict[int, RawSignalBundle] = {}

    try:
        with get_session_local()() as session:
            # 1. SceneDerived (view_count, derived_o_count, last_viewed_at)
            derived_rows = session.execute(
                sa.select(SceneDerived).where(
                    SceneDerived.scene_id.in_(normalized_ids),
                )
            ).scalars().all()
            for d in derived_rows:
                bundle = results.setdefault(d.scene_id, {})
                bundle["ai_view_count"] = d.view_count
                bundle["ai_derived_o_count"] = d.derived_o_count
                bundle["ai_last_viewed_at"] = d.last_viewed_at

            # 2. EntityRating (default key)
            # entity_id is VARCHAR; cast scene IDs to strings for the query
            str_ids = [str(sid) for sid in normalized_ids]
            rating_rows = session.execute(
                sa.select(EntityRating).where(
                    EntityRating.entity_type == entity_type,
                    EntityRating.entity_id.in_(str_ids),
                    EntityRating.rating_key == "default",
                )
            ).scalars().all()
            for r in rating_rows:
                try:
                    rid = int(r.entity_id)
                except (ValueError, TypeError):
                    continue
                bundle = results.setdefault(rid, {})
                bundle["ai_custom_rating"] = r.value

            # 3. SceneWatch aggregates (total_watched_s, watch_percent, session count)
            watch_stmt = (
                sa.select(
                    SceneWatch.scene_id.label("scene_id"),
                    sa.func.sum(SceneWatch.total_watched_s).label("total_watched_s"),
                    sa.func.max(SceneWatch.watch_percent).label("best_watch_percent"),
                    sa.func.count(SceneWatch.id).label("session_count"),
                    sa.func.max(SceneWatch.page_entered_at).label("last_entered"),
                )
                .where(SceneWatch.scene_id.in_(normalized_ids))
                .group_by(SceneWatch.scene_id)
            )
            for row in session.execute(watch_stmt):
                m = row._mapping
                try:
                    scene_id = int(m["scene_id"])
                except Exception:
                    continue
                bundle = results.setdefault(scene_id, {})
                bundle["ai_total_watched_s"] = float(m["total_watched_s"] or 0)
                bundle["ai_best_watch_percent"] = float(m["best_watch_percent"] or 0)
                bundle["ai_session_count"] = int(m["session_count"] or 0)
                bundle["ai_last_entered"] = m.get("last_entered")

    except Exception:
        _log.exception("Failed to load AI Server data for engagement scoring")

    return results


# ---------------------------------------------------------------------------
# Segment data loader
# ---------------------------------------------------------------------------

def load_scene_watch_segments(scene_id: int) -> list[dict[str, float]]:
    """Load all watch segments for a single scene, merged across sessions.

    Returns list of {"start_s": float, "end_s": float, "watched_s": float}.
    """
    try:
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(
                    SceneWatchSegment.start_s,
                    SceneWatchSegment.end_s,
                    SceneWatchSegment.watched_s,
                )
                .where(SceneWatchSegment.scene_id == int(scene_id))
                .order_by(SceneWatchSegment.start_s.asc())
            ).all()
            return [
                {
                    "start_s": float(r.start_s),
                    "end_s": float(r.end_s),
                    "watched_s": float(r.watched_s),
                }
                for r in rows
            ]
    except Exception:
        _log.exception("Failed to load watch segments for scene %s", scene_id)
        return []


# ---------------------------------------------------------------------------
# Combined loader
# ---------------------------------------------------------------------------

def _compute_rating_stats(
    stash_data: dict[int, RawSignalBundle],
    ai_data: dict[int, RawSignalBundle],
) -> tuple[float | None, float | None]:
    """Compute mean and std of all ratings across both sources.

    This lets the rating signal z-score normalize each scene's rating
    against the user's own distribution, adapting to different rating
    styles (5-star whole increments vs decimal, generous vs stingy).
    """
    all_ratings: list[float] = []

    # Prefer AI custom rating per scene; fall back to Stash rating
    all_ids = set(stash_data.keys()) | set(ai_data.keys())
    for sid in all_ids:
        ai_bundle = ai_data.get(sid, {})
        stash_bundle = stash_data.get(sid, {})
        custom = ai_bundle.get("ai_custom_rating")
        stash_r = stash_bundle.get("stash_rating")
        r = custom if custom is not None else stash_r
        if r is not None:
            try:
                all_ratings.append(float(r))
            except (TypeError, ValueError):
                pass

    if len(all_ratings) < 2:
        return None, None

    mean = sum(all_ratings) / len(all_ratings)
    variance = sum((x - mean) ** 2 for x in all_ratings) / len(all_ratings)
    std = variance ** 0.5
    return mean, std


def _load_all_rating_values() -> list[float]:
    """Load ALL rating values (not just for requested scenes) for stats.

    We need the full distribution to properly z-score normalize, not just
    the ratings of the scenes being scored.
    """
    all_ratings: list[float] = []

    # AI Server EntityRating (default key)
    try:
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(EntityRating.value).where(
                    EntityRating.rating_key == "default",
                    EntityRating.value.isnot(None),
                )
            ).scalars().all()
            for v in rows:
                try:
                    all_ratings.append(float(v))
                except (TypeError, ValueError):
                    pass
    except Exception:
        _log.debug("Could not load AI Server ratings for stats")

    # Stash DB ratings
    try:
        session_factory = stash_db.get_stash_sessionmaker()
        scenes_table = stash_db.get_stash_table("scenes", required=False)
        if session_factory and scenes_table is not None:
            rating_col = _pick_col(scenes_table, "rating")
            if rating_col is not None:
                with session_factory() as session:
                    rows = session.execute(
                        sa.select(rating_col).where(rating_col.isnot(None))
                    ).scalars().all()
                    for v in rows:
                        try:
                            rv = float(v)
                            if rv > 0:
                                all_ratings.append(rv)
                        except (TypeError, ValueError):
                            pass
    except Exception:
        _log.debug("Could not load Stash ratings for stats")

    return all_ratings


def _compute_global_rating_stats() -> tuple[float | None, float | None]:
    """Compute mean/std across ALL user ratings (both databases)."""
    all_ratings = _load_all_rating_values()
    if len(all_ratings) < 2:
        return None, None
    mean = sum(all_ratings) / len(all_ratings)
    variance = sum((x - mean) ** 2 for x in all_ratings) / len(all_ratings)
    std = variance ** 0.5
    return mean, std


def _compute_global_watch_stats() -> tuple[float | None, float | None]:
    """Compute mean/std of best-session watch percentages across all scenes.

    Only considers sessions where total_watched_s > 15 seconds to filter
    out accidental clicks, organization browsing, and non-real watches.
    Returns the stats for the per-scene *best session* watch_percent.
    """
    values: list[float] = []
    try:
        with get_session_local()() as session:
            stmt = (
                sa.select(
                    SceneWatch.scene_id,
                    sa.func.max(SceneWatch.watch_percent).label("best_pct"),
                )
                .where(SceneWatch.total_watched_s > 15)
                .group_by(SceneWatch.scene_id)
            )
            for row in session.execute(stmt):
                pct = row.best_pct
                if pct is not None and pct > 0:
                    values.append(float(pct))
    except Exception:
        _log.debug("Could not compute watch completeness stats")

    if len(values) < 2:
        return None, None
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std = variance ** 0.5
    return mean, std


def load_engagement_data(
    scene_ids: Sequence[int],
    entity_type: str = "scene",
) -> dict[int, RawSignalBundle]:
    """Load and merge engagement data from all sources for the given IDs.

    Merges Stash DB and AI Server data into a single bundle per scene.
    When both sources provide similar data (o-counts, ratings, recency),
    both are kept and the scorer merges them appropriately.
    """
    stash_data = _load_stash_data(scene_ids)
    ai_data = _load_ai_server_data(scene_ids, entity_type=entity_type)

    # Compute global stats for z-score normalization
    rating_mean, rating_std = _compute_global_rating_stats()
    watch_mean, watch_std = _compute_global_watch_stats()

    # Merge into a single bundle per scene
    all_ids = set(stash_data.keys()) | set(ai_data.keys())
    merged: dict[int, RawSignalBundle] = {}
    for sid in all_ids:
        bundle: RawSignalBundle = {}
        if sid in stash_data:
            bundle.update(stash_data[sid])
        if sid in ai_data:
            bundle.update(ai_data[sid])

        # Resolve scene_duration: prefer Stash DB, fall back to AI data
        duration = bundle.get("stash_duration")
        if not duration or duration <= 0:
            # Could be provided by caller or fetched elsewhere
            pass
        bundle["scene_duration"] = duration

        # Inject distribution stats for z-score normalization
        bundle["rating_mean"] = rating_mean
        bundle["rating_std"] = rating_std
        bundle["watch_completeness_mean"] = watch_mean
        bundle["watch_completeness_std"] = watch_std

        # Merge ratings: prefer custom AI rating, then Stash
        custom_rating = bundle.get("ai_custom_rating")
        stash_rating = bundle.get("stash_rating")
        if custom_rating is not None:
            bundle["explicit_rating"] = custom_rating
        elif stash_rating is not None:
            bundle["explicit_rating"] = stash_rating
        else:
            bundle["explicit_rating"] = None

        # Merge o-counts: take the max
        stash_oc = bundle.get("stash_o_counter") or 0
        ai_oc = bundle.get("ai_derived_o_count") or 0
        bundle["merged_o_counter"] = max(stash_oc, ai_oc) or None

        # Merge play/view counts: take the max
        stash_pc = bundle.get("stash_play_count") or 0
        ai_vc = bundle.get("ai_view_count") or 0
        bundle["merged_play_count"] = max(stash_pc, ai_vc) or None

        # Recency: take the most recent timestamp
        stash_last = bundle.get("stash_last_played_at")
        ai_last = bundle.get("ai_last_viewed_at")
        ai_entered = bundle.get("ai_last_entered")
        candidates = [d for d in [stash_last, ai_last, ai_entered] if d is not None]
        most_recent = max(candidates) if candidates else None
        bundle["most_recent_view"] = most_recent
        bundle["days_since_view"] = _days_ago(most_recent)

        # Watch data aggregation
        ai_total_watched = bundle.get("ai_total_watched_s") or 0
        stash_pd = bundle.get("stash_play_duration") or 0
        bundle["merged_total_watched_s"] = max(ai_total_watched, stash_pd) or None

        merged[sid] = bundle

    return merged
