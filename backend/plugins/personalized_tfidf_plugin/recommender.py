from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import sqlalchemy as sa
from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.ai_results import AIModelRun, AIResultAggregate
from stash_ai_server.models.interaction import SceneWatch, SceneWatchSegment
from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.recommendations.registry import recommender
from stash_ai_server.recommendations.utils.scene_fetch import fetch_scenes_by_ids
from stash_ai_server.utils import stash_db

_log = logging.getLogger(__name__)

# Default parameters tuned for an initial MVP pass.
DEFAULT_SERVICE = "AI_Tagging"
DEFAULT_MIN_WATCH_SECONDS = 30.0
DEFAULT_RECENT_DAYS = 45.0
DEFAULT_HISTORY_LIMIT = 400
DEFAULT_PROFILE_TAG_LIMIT = 12
DEFAULT_CANDIDATE_POOL = 200
DEFAULT_TOP_CONTRIBS = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

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


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_watch_history(
    *,
    recent_cutoff: datetime | None,
    min_watch_seconds: float,
    history_limit: int,
) -> List[Dict[str, Any]]:
    stmt = (
        sa.select(
            SceneWatch.scene_id.label("scene_id"),
            sa.func.sum(SceneWatchSegment.watched_s).label("watched_s"),
            sa.func.max(SceneWatch.page_entered_at).label("last_entered"),
            sa.func.max(SceneWatch.page_left_at).label("last_left"),
            sa.func.max(SceneWatchSegment.created_at).label("last_segment"),
        )
        .join(SceneWatchSegment, SceneWatchSegment.scene_watch_id == SceneWatch.id)
        .group_by(SceneWatch.scene_id)
    )
    if recent_cutoff is not None:
        stmt = stmt.where(SceneWatch.page_entered_at >= recent_cutoff)
    if min_watch_seconds > 0:
        stmt = stmt.having(sa.func.sum(SceneWatchSegment.watched_s) >= min_watch_seconds)
    stmt = stmt.order_by(sa.func.max(SceneWatch.page_entered_at).desc())
    if history_limit > 0:
        stmt = stmt.limit(history_limit)

    history: List[Dict[str, Any]] = []
    with SessionLocal() as session:
        for row in session.execute(stmt):
            watched_s = float(row.watched_s or 0.0)
            if watched_s <= 0:
                continue
            scene_id = int(row.scene_id)
            last_seen_raw = row.last_left or row.last_entered or row.last_segment
            last_seen = _ensure_utc(last_seen_raw)
            history.append(
                {
                    "scene_id": scene_id,
                    "watched_s": watched_s,
                    "last_seen": last_seen,
                    "source": "plugin",
                    "weight_mode": "observed_duration",
                }
            )
    return history


def _parse_stash_datetime(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        normalized = normalized.replace("T", " ", 1) if "T" in normalized and "+" not in normalized else normalized
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _load_stash_watch_history(
    *,
    recent_cutoff: datetime | None,
    min_watch_seconds: float,
    history_limit: int,
) -> List[Dict[str, Any]]:
    session_factory = stash_db.get_stash_sessionmaker()
    scenes_table = stash_db.get_stash_table("scenes", required=False)
    if session_factory is None or scenes_table is None:
        return []

    view_table = stash_db.get_stash_table("scenes_view_dates", required=False)
    fetch_limit = history_limit * 3 if history_limit > 0 else None
    results: List[Dict[str, Any]] = []

    try:
        with session_factory() as session:
            rows: Sequence[Any] = []
            used_view_table = False
            if (
                view_table is not None
                and view_table.c.get("scene_id") is not None
                and view_table.c.get("view_date") is not None
            ):
                scene_id_col = view_table.c.get("scene_id")
                view_date_col = view_table.c.get("view_date")
                play_duration_col = scenes_table.c.get("play_duration")
                stmt = (
                    sa.select(
                        scene_id_col.label("scene_id"),
                        sa.func.max(view_date_col).label("last_view"),
                        sa.func.count(view_date_col).label("view_count"),
                        play_duration_col.label("play_duration"),
                    )
                    .select_from(view_table.join(scenes_table, scene_id_col == scenes_table.c.id))
                    .group_by(scene_id_col, play_duration_col)
                    .order_by(sa.func.max(view_date_col).desc())
                )
                if fetch_limit is not None:
                    stmt = stmt.limit(fetch_limit)
                rows = session.execute(stmt).all()
                used_view_table = True
            if not rows:
                play_duration_col = scenes_table.c.get("play_duration")
                if play_duration_col is None:
                    return []
                last_col = scenes_table.c.get("updated_at") or scenes_table.c.get("created_at")
                fallback_stmt = (
                    sa.select(
                        scenes_table.c.id.label("scene_id"),
                        (last_col.label("last_view") if last_col is not None else sa.literal(None).label("last_view")),
                        sa.literal(0).label("view_count"),
                        play_duration_col.label("play_duration"),
                    )
                    .where(play_duration_col.isnot(None), play_duration_col > 0)
                    .order_by(play_duration_col.desc())
                )
                if fetch_limit is not None:
                    fallback_stmt = fallback_stmt.limit(fetch_limit)
                rows = session.execute(fallback_stmt).all()
                if rows:
                    _log.debug(
                        "personalized_tfidf: stash watch fallback using scenes.play_duration",
                        extra={"row_count": len(rows), "used_view_table": used_view_table},
                    )

            for row in rows:
                try:
                    scene_id = int(row.scene_id)
                except Exception:
                    continue
                last_seen = _parse_stash_datetime(getattr(row, "last_view", None))
                if recent_cutoff is not None and last_seen is not None and last_seen < recent_cutoff:
                    continue
                view_count = getattr(row, "view_count", 0) or 0
                try:
                    view_count_int = int(view_count)
                except Exception:
                    view_count_int = 0
                play_duration_total = float(getattr(row, "play_duration", 0.0) or 0.0)
                estimated_for_filter = play_duration_total if play_duration_total > 0 else view_count_int * 60.0
                if min_watch_seconds > 0 and estimated_for_filter < min_watch_seconds:
                    continue
                if play_duration_total <= 0 and view_count_int <= 0:
                    continue
                weight_mode = "total_duration" if play_duration_total > 0 else "view_count"
                results.append(
                    {
                        "scene_id": scene_id,
                        "watched_s": play_duration_total,
                        "view_count": view_count_int if view_count_int > 0 else None,
                        "last_seen": last_seen,
                        "source": "stash",
                        "weight_mode": weight_mode,
                    }
                )
    except Exception:  # pragma: no cover - defensive fallback
        _log.exception("personalized_tfidf: failed to load stash watch history")
        return []

    if not results:
        return []

    results.sort(
        key=lambda entry: (entry.get("last_seen") or datetime.fromtimestamp(0, tz=timezone.utc)),
        reverse=True,
    )
    if history_limit > 0:
        return results[:history_limit]
    return results


def _fetch_tag_durations_for_scenes(
    *,
    service: str,
    scene_ids: Sequence[int],
) -> Tuple[Dict[int, Dict[int, float]], set[int]]:
    if not scene_ids:
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
            AIResultAggregate.entity_id.in_(scene_ids),
        )
        .group_by(AIResultAggregate.entity_id, AIResultAggregate.value_id)
    )
    per_scene: Dict[int, Dict[int, float]] = defaultdict(dict)
    tag_ids: set[int] = set()
    with SessionLocal() as session:
        for row in session.execute(stmt):
            tag = row.tag_id
            if tag is None:
                continue
            scene_id = int(row.scene_id)
            tag_id = int(tag)
            duration_val = float(row.duration_s or 0.0)
            if duration_val <= 0:
                continue
            per_scene[scene_id][tag_id] = duration_val
            tag_ids.add(tag_id)
    return per_scene, tag_ids


def _fetch_corpus_stats(
    *,
    service: str,
    tag_ids: Iterable[int],
) -> Tuple[Dict[int, Dict[str, float]], int]:
    tag_list = [int(tag_id) for tag_id in tag_ids]
    if not tag_list:
        return {}, 0
    stats_stmt = (
        sa.select(
            AIResultAggregate.value_id.label("tag_id"),
            sa.func.count(sa.distinct(AIResultAggregate.entity_id)).label("scene_count"),
            sa.func.sum(AIResultAggregate.value_float).label("total_duration_s"),
        )
        .join(AIModelRun, AIResultAggregate.run_id == AIModelRun.id)
        .where(
            AIModelRun.service == service,
            AIModelRun.entity_type == "scene",
            AIResultAggregate.payload_type == "tag",
            AIResultAggregate.metric == "duration_s",
            AIResultAggregate.value_id.in_(tag_list),
        )
        .group_by(AIResultAggregate.value_id)
    )
    total_stmt = (
        sa.select(sa.func.count(sa.distinct(AIResultAggregate.entity_id)))
        .join(AIModelRun, AIResultAggregate.run_id == AIModelRun.id)
        .where(
            AIModelRun.service == service,
            AIModelRun.entity_type == "scene",
            AIResultAggregate.payload_type == "tag",
            AIResultAggregate.metric == "duration_s",
        )
    )
    stats: Dict[int, Dict[str, float]] = {}
    with SessionLocal() as session:
        total_scenes = int(session.execute(total_stmt).scalar_one() or 0)
        for row in session.execute(stats_stmt):
            tag_id = int(row.tag_id)
            stats[tag_id] = {
                "scene_count": int(row.scene_count or 0),
                "total_duration_s": float(row.total_duration_s or 0.0),
            }
    return stats, total_scenes


def _load_tag_lookup(tag_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    tag_list = [int(tag_id) for tag_id in tag_ids]
    if not tag_list:
        return {}
    table = stash_db.get_stash_table("tags", required=False)
    session_factory = stash_db.get_stash_sessionmaker()
    if table is None or session_factory is None:
        return {}
    stmt = sa.select(table.c.id, table.c.name).where(table.c.id.in_(tag_list))
    lookup: Dict[int, Dict[str, Any]] = {}
    with session_factory() as session:
        for row in session.execute(stmt):
            try:
                lookup[int(row.id)] = {"name": row.name}
            except Exception:
                continue
    return lookup


def _rank_candidates(
    *,
    service: str,
    tag_weights: Mapping[int, float],
    watched_scene_ids: set[int],
    candidate_limit: int,
    per_tag_limit: int,
) -> Tuple[List[Tuple[int, float]], Dict[int, List[Tuple[int, float]]]]:
    if not tag_weights:
        return [], {}
    candidate_scores: Dict[int, float] = defaultdict(float)
    tag_contribs: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    ordered_tags = sorted(tag_weights.items(), key=lambda item: item[1], reverse=True)
    with SessionLocal() as session:
        for tag_id, weight in ordered_tags:
            if weight <= 0:
                continue
            tag_stmt = (
                sa.select(
                    AIResultAggregate.entity_id.label("scene_id"),
                    sa.func.sum(AIResultAggregate.value_float).label("duration_s"),
                )
                .join(AIModelRun, AIResultAggregate.run_id == AIModelRun.id)
                .where(
                    AIModelRun.service == service,
                    AIModelRun.entity_type == "scene",
                    AIResultAggregate.payload_type == "tag",
                    AIResultAggregate.metric == "duration_s",
                    AIResultAggregate.value_id == tag_id,
                )
                .group_by(AIResultAggregate.entity_id)
                .order_by(sa.func.sum(AIResultAggregate.value_float).desc())
                .limit(per_tag_limit)
            )
            for row in session.execute(tag_stmt):
                scene_id = int(row.scene_id)
                if scene_id in watched_scene_ids:
                    continue
                duration_val = float(row.duration_s or 0.0)
                if duration_val <= 0:
                    continue
                contribution = weight * duration_val
                candidate_scores[scene_id] += contribution
                tag_contribs[scene_id].append((tag_id, duration_val))
    ranked = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)
    if candidate_limit > 0:
        ranked = ranked[:candidate_limit]
    return ranked, tag_contribs


@recommender(
    id="personalized_tfidf",
    label="Personalized TF-IDF",
    description="Recommends scenes aligned with recently watched tag durations.",
    contexts=[RecContext.global_feed],
    config=[
        {
            "name": "recent_days",
            "label": "Recent Days",
            "type": "number",
            "default": DEFAULT_RECENT_DAYS,
            "min": 1,
            "max": 365,
            "help": "Only consider watches newer than this window.",
        },
        {
            "name": "min_watch_seconds",
            "label": "Min Watched Seconds",
            "type": "number",
            "default": DEFAULT_MIN_WATCH_SECONDS,
            "min": 0,
            "max": 7200,
        },
        {
            "name": "history_limit",
            "label": "History Scene Limit",
            "type": "number",
            "default": DEFAULT_HISTORY_LIMIT,
            "min": 25,
            "max": 1000,
        },
        {
            "name": "profile_tag_limit",
            "label": "Profile Tag Limit",
            "type": "number",
            "default": DEFAULT_PROFILE_TAG_LIMIT,
            "min": 3,
            "max": 40,
        },
        {
            "name": "candidate_pool",
            "label": "Candidate Pool",
            "type": "number",
            "default": DEFAULT_CANDIDATE_POOL,
            "min": 40,
            "max": 800,
        },
    ],
    supports_pagination=True,
    exposes_scores=True,
)
async def personalized_tfidf(ctx: Dict[str, Any], request: RecommendationRequest):
    cfg = request.config or {}
    seed_ids = [int(sid) for sid in request.seedSceneIds or [] if sid is not None]
    recent_days = _coerce_float(cfg.get("recent_days"), DEFAULT_RECENT_DAYS)
    recent_cutoff = _utc_now() - timedelta(days=recent_days) if recent_days > 0 else None
    min_watch_seconds = max(0.0, _coerce_float(cfg.get("min_watch_seconds"), DEFAULT_MIN_WATCH_SECONDS))
    history_limit = max(0, _coerce_int(cfg.get("history_limit"), DEFAULT_HISTORY_LIMIT))
    profile_tag_limit = max(1, _coerce_int(cfg.get("profile_tag_limit"), DEFAULT_PROFILE_TAG_LIMIT))
    candidate_pool = max(20, _coerce_int(cfg.get("candidate_pool"), DEFAULT_CANDIDATE_POOL))
    service_name = DEFAULT_SERVICE

    history = _load_watch_history(
        recent_cutoff=recent_cutoff,
        min_watch_seconds=min_watch_seconds,
        history_limit=history_limit,
    )
    plugin_history_count = len(history)
    history_by_scene = {entry["scene_id"]: entry for entry in history}

    stash_history = _load_stash_watch_history(
        recent_cutoff=recent_cutoff,
        min_watch_seconds=min_watch_seconds,
        history_limit=history_limit,
    )
    stash_history_count = len(stash_history)
    if stash_history:
        appended = 0
        for stash_entry in stash_history:
            sid = stash_entry["scene_id"]
            existing = history_by_scene.get(sid)
            if existing:
                existing_watched = float(existing.get("watched_s") or 0.0)
                stash_watched = float(stash_entry.get("watched_s") or 0.0)
                if stash_watched > 0:
                    existing["watched_s"] = existing_watched + stash_watched
                stash_views = stash_entry.get("view_count") or 0
                if stash_views:
                    existing["view_count"] = (existing.get("view_count") or 0) + stash_views
                    existing_last_seen = _ensure_utc(existing.get("last_seen"))
                    if existing_last_seen is not None:
                        existing["last_seen"] = existing_last_seen
                    stash_last_seen = _ensure_utc(stash_entry.get("last_seen"))
                if stash_last_seen and (existing_last_seen is None or stash_last_seen > existing_last_seen):
                    existing["last_seen"] = stash_last_seen
                combined_source = existing.get("source") or "plugin"
                if "stash" not in combined_source:
                    existing["source"] = f"{combined_source}+stash"
                else:
                    existing["source"] = combined_source
                existing["weight_mode"] = "combined"
                if stash_entry.get("weight_mode"):
                    existing["stash_weight_mode"] = stash_entry.get("weight_mode")
            else:
                stash_entry["last_seen"] = _ensure_utc(stash_entry.get("last_seen"))
                history.append(stash_entry)
                history_by_scene[sid] = stash_entry
                appended += 1
        if appended or plugin_history_count == 0:
            _log.debug(
                "personalized_tfidf: incorporated stash watch history",
                extra={
                    "plugin_history_count": plugin_history_count,
                    "stash_history_count": stash_history_count,
                    "new_entries": appended,
                },
            )

    history.sort(
        key=lambda entry: (
            entry.get("last_seen") or datetime.fromtimestamp(0, tz=timezone.utc),
            float(entry.get("watched_s") or 0.0),
        ),
        reverse=True,
    )
    if history_limit > 0 and len(history) > history_limit:
        history = history[:history_limit]
        history_by_scene = {entry["scene_id"]: entry for entry in history}

    if not history and seed_ids:
        _log.info("personalized_tfidf: watch history empty, seeding profile from provided seeds", extra={"seed_count": len(seed_ids)})

    for sid in seed_ids:
        if sid in history_by_scene:
            continue
        entry = {
            "scene_id": sid,
            "watched_s": 0.0,
            "last_seen": None,
            "source": "seed",
            "weight_mode": "seed_duration",
        }
        history.append(entry)
        history_by_scene[sid] = entry

    history.sort(
        key=lambda entry: (
            entry.get("last_seen") or datetime.fromtimestamp(0, tz=timezone.utc),
            float(entry.get("watched_s") or 0.0),
        ),
        reverse=True,
    )
    if history_limit > 0 and len(history) > history_limit:
        history = history[:history_limit]
        history_by_scene = {entry["scene_id"]: entry for entry in history}

    history_scene_ids = [entry["scene_id"] for entry in history]
    combined_scene_ids = list(dict.fromkeys(history_scene_ids + seed_ids))

    tag_durations, tag_ids = _fetch_tag_durations_for_scenes(service=service_name, scene_ids=combined_scene_ids)
    if tag_durations:
        _log.debug(
            "personalized_tfidf: fetched tag durations",
            extra={
                "scenes_with_tags": len(tag_durations),
                "unique_tags": len(tag_ids),
            },
        )
    if not tag_durations:
        _log.info(
            "personalized_tfidf: no tag durations available",
            extra={
                "history_count": len(history_scene_ids),
                "seed_count": len(seed_ids),
            },
        )
        return {"scenes": [], "total": 0, "has_more": False}
    history_by_scene = {entry["scene_id"]: entry for entry in history}
    seeds_to_remove: List[int] = []
    for sid in seed_ids:
        entry = history_by_scene.get(sid)
        if entry is None:
            continue
        tag_map = tag_durations.get(sid)
        if not tag_map:
            seeds_to_remove.append(sid)
            continue
        pseudo_watch = sum(max(0.0, float(val)) for val in tag_map.values())
        if pseudo_watch <= 0:
            seeds_to_remove.append(sid)
            continue
        entry["watched_s"] = pseudo_watch
        entry["weight_mode"] = "seed_duration"
    if seeds_to_remove:
        remove_set = set(seeds_to_remove)
        history = [entry for entry in history if entry["scene_id"] not in remove_set]
        history_by_scene = {entry["scene_id"]: entry for entry in history}
        history_scene_ids = [entry["scene_id"] for entry in history]

    if not history:
        _log.info("personalized_tfidf: no usable watch data after merging sources")
        return {"scenes": [], "total": 0, "has_more": False}

    watched_ids = [entry["scene_id"] for entry in history]
    watched_set = set(watched_ids)
    scene_payloads = fetch_scenes_by_ids(watched_ids)

    corpus_stats, total_corpus_scenes = _fetch_corpus_stats(service=service_name, tag_ids=tag_ids)
    if not corpus_stats or total_corpus_scenes <= 0:
        _log.info(
            "personalized_tfidf: missing corpus stats",
            extra={"tag_count": len(tag_ids), "total_corpus_scenes": total_corpus_scenes},
        )
        return {"scenes": [], "total": 0, "has_more": False}

    tf_values: Dict[int, float] = defaultdict(float)
    for entry in history:
        scene_id = entry["scene_id"]
        tag_map = tag_durations.get(scene_id)
        if not tag_map:
            continue
        scene_payload = scene_payloads.get(scene_id)
        scene_duration = None
        if scene_payload is not None:
            duration_val = scene_payload.get("duration")
            try:
                scene_duration = float(duration_val) if duration_val is not None else None
            except (TypeError, ValueError):
                scene_duration = None
        watched_s_value = float(entry.get("watched_s") or 0.0)
        view_count = entry.get("view_count") or 0
        try:
            view_count = int(view_count)
        except Exception:
            view_count = 0
        weight_mode = entry.get("weight_mode") or entry.get("stash_weight_mode") or "observed_duration"
        if weight_mode in {"view_count", "combined"} and scene_duration and scene_duration > 0:
            if view_count <= 0:
                view_count = 1
            if watched_s_value <= 0 or weight_mode == "view_count":
                watched_s_value = scene_duration * view_count
        elif watched_s_value <= 0 and scene_duration and scene_duration > 0:
            watched_s_value = scene_duration

        repeat_factor: float
        if scene_duration and scene_duration > 0 and watched_s_value > 0:
            repeat_factor = max(watched_s_value / scene_duration, 0.0)
        elif view_count > 0:
            repeat_factor = float(view_count)
        else:
            repeat_factor = 1.0
        if repeat_factor <= 0:
            continue

        for tag_id, duration_s in tag_map.items():
            try:
                duration_val = float(duration_s)
            except (TypeError, ValueError):
                continue
            if duration_val <= 0:
                continue
            base_overlap = duration_val
            if watched_s_value > 0:
                base_overlap = min(duration_val, watched_s_value)
            if base_overlap <= 0:
                continue
            tf_values[tag_id] += base_overlap * repeat_factor

    if not tf_values:
        _log.info("personalized_tfidf: no tf contributions computed", extra={"history_count": len(history)})
        return {"scenes": [], "total": 0, "has_more": False}

    profile_weights: Dict[int, float] = {}
    for tag_id, tf_val in tf_values.items():
        if tf_val <= 0:
            continue
        stats = corpus_stats.get(tag_id)
        if not stats:
            continue
        doc_freq = max(1, stats.get("scene_count", 0))
        idf = math.log((1 + total_corpus_scenes) / (1 + doc_freq)) + 1.0
        profile_weights[tag_id] = tf_val * idf

    if not profile_weights:
        _log.info("personalized_tfidf: no profile weights after tf-idf", extra={"tf_tags": len(tf_values)})
        return {"scenes": [], "total": 0, "has_more": False}

    ordered_profile = sorted(profile_weights.items(), key=lambda item: item[1], reverse=True)
    top_profile = ordered_profile[:profile_tag_limit]
    if not top_profile:
        _log.info("personalized_tfidf: profile empty after applying tag limit", extra={"profile_tag_limit": profile_tag_limit})
        return {"scenes": [], "total": 0, "has_more": False}

    requested_offset = request.offset if isinstance(request.offset, int) and request.offset is not None else 0
    if requested_offset < 0:
        requested_offset = 0
    requested_limit = request.limit if isinstance(request.limit, int) and request.limit and request.limit > 0 else 40
    pool_target = max(candidate_pool, requested_limit + requested_offset + 20)
    per_tag_limit = max(10, pool_target // max(1, len(top_profile)))

    limited_weights = {tag_id: profile_weights[tag_id] for tag_id, _ in top_profile}
    _log.debug(
        "personalized_tfidf: generating candidates",
        extra={
            "history_count": len(history),
            "seed_count": len(seed_ids),
            "profile_tags": len(limited_weights),
            "plugin_history_count": plugin_history_count,
            "stash_history_count": stash_history_count,
            "pool_target": pool_target,
            "per_tag_limit": per_tag_limit,
        },
    )
    ranked_candidates, candidate_contribs = _rank_candidates(
        service=service_name,
        tag_weights=limited_weights,
        watched_scene_ids=watched_set,
        candidate_limit=pool_target,
        per_tag_limit=per_tag_limit,
    )
    if not ranked_candidates:
        _log.info("personalized_tfidf: ranking produced no candidates", extra={"profile_tags": len(limited_weights)})
        return {"scenes": [], "total": 0, "has_more": False}

    candidate_ids = [scene_id for scene_id, _ in ranked_candidates]
    candidate_payloads = fetch_scenes_by_ids(candidate_ids)

    tag_lookup = _load_tag_lookup(limited_weights.keys())

    scenes_out: List[Dict[str, Any]] = []
    skipped_payload = 0
    source_breakdown = {"plugin": 0, "stash": 0, "seed": 0}
    for entry in history:
        source = (entry.get("source") or "").lower()
        if "plugin" in source:
            source_breakdown["plugin"] += 1
        if "stash" in source:
            source_breakdown["stash"] += 1
        if source.startswith("seed"):
            source_breakdown["seed"] += 1
    profile_summary = {
        "history_count": len(history),
        "seed_count": len(seed_ids),
        "profile_tags": len(limited_weights),
        "plugin_history_count": plugin_history_count,
        "stash_history_count": stash_history_count,
        "history_sources": source_breakdown,
    }
    for scene_id, score in ranked_candidates:
        payload = candidate_payloads.get(scene_id)
        if not payload:
            skipped_payload += 1
            continue
        scene_copy = dict(payload)
        scene_copy["score"] = round(float(score), 6)
        debug_meta = dict(scene_copy.get("debug_meta") or {})
        contribs = candidate_contribs.get(scene_id, [])
        contrib_details = []
        for tag_id, duration_s in sorted(
            contribs,
            key=lambda item: limited_weights.get(item[0], 0.0) * item[1],
            reverse=True,
        )[:DEFAULT_TOP_CONTRIBS]:
            contrib_details.append(
                {
                    "tag_id": tag_id,
                    "tag_name": tag_lookup.get(tag_id, {}).get("name"),
                    "duration_s": round(duration_s, 3),
                    "profile_weight": round(limited_weights.get(tag_id, 0.0), 4),
                    "partial_score": round(limited_weights.get(tag_id, 0.0) * duration_s, 4),
                }
            )
        debug_meta["tfidf"] = {
            "profile_size": len(limited_weights),
            "scene_contributors": contrib_details,
            "profile_summary": profile_summary,
        }
        scene_copy["debug_meta"] = debug_meta
        scenes_out.append(scene_copy)

    if skipped_payload:
        _log.debug("personalized_tfidf: skipped candidates without payload", extra={"skipped": skipped_payload})

    total_available = len(scenes_out)
    if total_available == 0:
        _log.info("personalized_tfidf: no candidates retained after payload lookup")
        return {"scenes": [], "total": 0, "has_more": False}

    start = min(requested_offset, total_available)
    end = start + requested_limit if requested_limit > 0 else total_available
    page = scenes_out[start:end]
    has_more = end < total_available

    _log.debug(
        "personalized_tfidf: returning page",
        extra={
            "page_size": len(page),
            "total_available": total_available,
            "has_more": has_more,
            "offset": start,
            "limit": requested_limit,
        },
    )

    return {
        "scenes": page,
        "total": total_available,
        "has_more": has_more,
    }
