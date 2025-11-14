from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple
from urllib.parse import urlencode

import sqlalchemy as sa

from stash_ai_server.utils import stash_db
from stash_ai_server.utils.stash_api import stash_api

_log = logging.getLogger(__name__)


# GraphQL fallback intentionally removed: use pure SQL path only


def _stub_scene(scene_id: int) -> Dict[str, Any]:
    return {
        "id": int(scene_id),
        "title": f"Scene {scene_id}",
        "rating100": None,
        "studio": None,
        "paths": {"screenshot": None, "preview": None, "stream": None, "webp": None},
        "duration": None,
        "performers": [],
        "tags": [],
        "files": [],
        "series": [],
    }


def _normalize_scene_payload(scene: Dict[str, Any]) -> Dict[str, Any]:
    paths = scene.setdefault("paths", {}) if isinstance(scene, dict) else {}
    if isinstance(paths, dict):
        paths.setdefault("screenshot", None)
        paths.setdefault("preview", None)
        paths.setdefault("stream", None)
        paths.setdefault("webp", None)
    scene.setdefault("performers", [])
    scene.setdefault("tags", [])
    scene.setdefault("files", [])
    scene.setdefault("series", [])
    return scene


def _column_with_default(table: sa.Table, column_name: str, *, alias: str | None = None, default: Any = None) -> sa.ColumnElement[Any]:
    column = table.c.get(column_name)
    label = alias or column_name
    if column is None:
        return sa.literal(default).label(label)
    return column.label(label)


def _pick_column(table: sa.Table | None, *names: str):
    if table is None:
        return None
    for n in names:
        c = table.c.get(n)
        if c is not None:
            return c
    return None


def _label_or_literal(column: sa.ColumnElement[Any] | None, alias: str, default: Any = None) -> sa.ColumnElement[Any]:
    if column is None:
        return sa.literal(default).label(alias)
    try:
        return column.label(alias)
    except Exception:
        return sa.literal(default).label(alias)


def _build_scene_url(scene_id: int, endpoint: str, *, include_api_key: bool = False, params: Dict[str, Any] | None = None) -> str | None:
    base = stash_api.stash_url
    if not base:
        return None
    base = base.rstrip("/")
    query: Dict[str, Any] = {}
    if params:
        query.update({k: v for k, v in params.items() if v not in (None, "")})
    if include_api_key and stash_api.api_key:
        query.setdefault("apikey", stash_api.api_key)
    query_string = urlencode(query) if query else ""
    suffix = f"?{query_string}" if query_string else ""
    return f"{base}/scene/{scene_id}/{endpoint}{suffix}"


def _build_scene_paths(scene_id: int) -> Dict[str, str]:
    return {
        key: value
        for key, value in {
            "screenshot": _build_scene_url(scene_id, "screenshot"),
            "preview": _build_scene_url(scene_id, "preview"),
            "stream": _build_scene_url(scene_id, "stream", include_api_key=True),
            "webp": _build_scene_url(scene_id, "webp"),
        }.items()
        if value
    }


def _coerce_unix_timestamp(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        try:
            return int(float(raw))
        except Exception:
            return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            try:
                return int(text)
            except Exception:
                return None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            return int(dt.timestamp())
        except Exception:
            return None
    return None


def _build_performer_image_url(performer_id: int, *, updated_at: Any = None) -> str | None:
    base = stash_api.stash_url
    if not base:
        return None
    base = base.rstrip("/")
    query: Dict[str, Any] = {"default": "true"}
    ts = _coerce_unix_timestamp(updated_at)
    if ts is not None:
        query["t"] = str(ts)
    query_string = urlencode(query) if query else ""
    suffix = f"?{query_string}" if query_string else ""
    return f"{base}/performer/{performer_id}/image{suffix}"


def fetch_scene_candidates_by_performers(
    *,
    performer_ids: Sequence[int],
    exclude_scene_ids: Iterable[int] | None = None,
    limit: int | None = 400,
) -> List[Tuple[int, Set[int]]]:
    """Return candidate scene ids keyed by matching performers.

    Results are sorted by descending number of shared performers and then by
    scene id. ``exclude_scene_ids`` can be used to strip out already-watched or
    otherwise ineligible scenes before scoring downstream.
    """

    normalized_performer_ids = [int(pid) for pid in performer_ids if pid is not None]
    if not normalized_performer_ids:
        return []

    exclude_set = {int(sid) for sid in exclude_scene_ids or [] if sid is not None}

    session_factory = stash_db.get_stash_sessionmaker()
    if session_factory is None:
        return []

    link_table = stash_db.get_first_available_table(
        "performers_scenes",
        "scene_performers",
        "performer_scenes",
        "performers_scene",
    )
    if link_table is None:
        return []

    scene_col = _pick_column(link_table, "scene_id", "sceneId")
    performer_col = _pick_column(link_table, "performer_id", "performerId")
    if scene_col is None or performer_col is None:
        return []

    stmt = sa.select(
        scene_col.label("scene_id"),
        performer_col.label("performer_id"),
    ).where(performer_col.in_(normalized_performer_ids))

    if limit is not None and limit > 0:
        approx_limit = limit * max(2, len(normalized_performer_ids))
        stmt = stmt.limit(approx_limit)

    candidate_map: Dict[int, Set[int]] = defaultdict(set)
    with session_factory() as session:
        for row in session.execute(stmt):
            try:
                scene_id = int(row.scene_id)
                performer_id = int(row.performer_id)
            except (TypeError, ValueError):
                continue
            if exclude_set and scene_id in exclude_set:
                continue
            candidate_map[scene_id].add(performer_id)

    if not candidate_map:
        return []

    ordered = sorted(candidate_map.items(), key=lambda item: (len(item[1]), item[0]), reverse=True)
    if limit is not None and limit > 0:
        ordered = ordered[:limit]
    return ordered


def _fetch_scenes_via_db(scene_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    normalized_ids = [int(sid) for sid in scene_ids if sid is not None]
    if not normalized_ids:
        return {}

    session_factory = stash_db.get_stash_sessionmaker()
    if session_factory is None:
        return {}

    scenes_table = stash_db.get_stash_table("scenes", required=False)
    if scenes_table is None:
        return {}

    id_column = scenes_table.c.get("id")
    if id_column is None:
        return {}

    try:
        with session_factory() as session:
            # Allow multiple possible column names for screenshots/previews
            screenshot_col = _pick_column(scenes_table, "screenshot_path", "screenshot", "thumb_path", "thumbnail")
            preview_col = _pick_column(scenes_table, "preview_path", "preview", "preview_path_on_disk")
            duration_col = _pick_column(
                scenes_table,
                "duration",
                "duration_s",
                "duration_seconds",
                "duration_sec",
                "duration_ms",
                "duration_milliseconds",
            )
            base_stmt = (
                sa.select(
                    id_column.label("id"),
                    _column_with_default(scenes_table, "title", alias="title"),
                    _column_with_default(scenes_table, "rating", alias="rating"),
                    _column_with_default(scenes_table, "studio_id", alias="studio_id"),
                    _label_or_literal(screenshot_col, "screenshot_path"),
                    _label_or_literal(preview_col, "preview_path"),
                    _label_or_literal(duration_col, "scene_duration"),
                    _column_with_default(scenes_table, "play_duration", alias="play_duration"),
                )
                .where(id_column.in_(normalized_ids))
            )
            base_rows = session.execute(base_stmt).all()
            if not base_rows:
                return {}

            scenes: Dict[int, Dict[str, Any]] = {}
            studio_ids: set[int] = set()
            for row in base_rows:
                mapping = row._mapping
                try:
                    scene_id = int(mapping["id"])
                except Exception:
                    continue
                raw_studio = mapping.get("studio_id")
                studio_id: int | None
                try:
                    studio_id = int(raw_studio) if raw_studio is not None else None
                except Exception:
                    studio_id = None
                if studio_id is not None:
                    studio_ids.add(studio_id)
                duration_value = None
                scene_duration_raw = mapping.get("scene_duration")
                if scene_duration_raw is not None:
                    try:
                        duration_value = float(scene_duration_raw)
                        if duration_col is not None and duration_col.name and duration_col.name.endswith("ms"):
                            duration_value /= 1000.0
                        if duration_value <= 0:
                            duration_value = None
                    except Exception:
                        duration_value = None
                if (duration_value is None or duration_value <= 0) and mapping.get("play_duration") not in (None, 0):
                    try:
                        alt = float(mapping.get("play_duration"))
                        if alt > 0:
                            duration_value = alt
                    except Exception:
                        pass
                    
                rating100_value = None
                legacy_rating = mapping.get("rating")
                if legacy_rating not in (None, ""):
                    try:
                        rating100_value = int(round(float(legacy_rating)))
                    except Exception:
                        rating100_value = None
                scenes[scene_id] = {
                    "id": scene_id,
                    "title": mapping.get("title"),
                    "rating100": rating100_value,
                    "studio": None,
                    "paths": {
                        "screenshot": mapping.get("screenshot_path"),
                        "preview": mapping.get("preview_path"),
                    },
                    "duration": duration_value,
                    "performers": [],
                    "tags": [],
                    "files": [],
                    "series": [],
                }
                if studio_id is not None:
                    scenes[scene_id]["_studio_id"] = studio_id

            if not scenes:
                return {}

            if studio_ids:
                studios_table = stash_db.get_stash_table("studios", required=False)
                if studios_table is not None:
                    studio_id_col = studios_table.c.get("id")
                    studio_name_col = studios_table.c.get("name")
                    if studio_id_col is not None and studio_name_col is not None:
                        studio_stmt = sa.select(
                            studio_id_col.label("id"),
                            studio_name_col.label("name"),
                        ).where(studio_id_col.in_(studio_ids))
                        studio_map = {
                            int(row.id): {"id": int(row.id), "name": row.name}
                            for row in session.execute(studio_stmt)
                            if row.id is not None
                        }
                    else:
                        studio_map = {}
                else:
                    studio_map = {}
                for scene in scenes.values():
                    studio_id = scene.pop("_studio_id", None)
                    if studio_id is not None:
                        scene["studio"] = studio_map.get(studio_id)
            else:
                for scene in scenes.values():
                    scene.pop("_studio_id", None)

            for scene_id, scene in scenes.items():
                default_paths = _build_scene_paths(scene_id)
                if default_paths:
                    existing_paths = scene.get("paths") or {}
                    if not isinstance(existing_paths, dict):
                        existing_paths = {}
                    merged = {**default_paths, **{k: v for k, v in existing_paths.items() if v}}
                    scene["paths"] = merged

            performer_link = stash_db.get_first_available_table(
                "performers_scenes", "scene_performers", "performer_scenes", required_columns=("scene_id", "performer_id")
            )
            performers_table = stash_db.get_stash_table("performers", required=False)
            if (
                performer_link is not None
                and performers_table is not None
                and performer_link.c.get("scene_id") is not None
                and performer_link.c.get("performer_id") is not None
                and performers_table.c.get("id") is not None
                and performers_table.c.get("name") is not None
            ):
                scene_col = performer_link.c.get("scene_id")
                performer_id_col = performer_link.c.get("performer_id")
                performer_id_target = performers_table.c.get("id")
                performer_name = performers_table.c.get("name")

                #TODO: stop guessing at stuff like this
                performer_image = _pick_column(performers_table, "image_path", "image")
                performer_stmt = (
                    sa.select(
                        scene_col.label("scene_id"),
                        performer_id_target.label("id"),
                        performer_name.label("name"),
                        _label_or_literal(performer_image, "image_path"),
                        _column_with_default(performers_table, "updated_at", alias="updated_at"),
                    )
                    .select_from(performer_link.join(performers_table, performer_id_target == performer_id_col))
                    .where(scene_col.in_(normalized_ids))
                )
                _log.debug("performer link table=%s performer table=%s cols=%s", getattr(performer_link, 'name', None), getattr(performers_table, 'name', None), {c.name for c in performer_link.c})
                for row in session.execute(performer_stmt):
                    try:
                        sid = int(row.scene_id)
                        pid = int(row.id)
                    except Exception:
                        continue
                    scene = scenes.get(sid)
                    if scene is None:
                        continue
                    image_url = row.image_path
                    if not image_url:
                        image_url = _build_performer_image_url(pid, updated_at=row.updated_at)
                    scene["performers"].append({
                        "id": pid,
                        "name": row.name,
                        "image_path": image_url,
                    })

            tag_link = stash_db.get_first_available_table(
                "scene_tags", "scenes_tags", "tags_scenes", required_columns=("scene_id", "tag_id")
            )
            tags_table = stash_db.get_stash_table("tags", required=False)
            if (
                tag_link is not None
                and tags_table is not None
                and tag_link.c.get("scene_id") is not None
                and tag_link.c.get("tag_id") is not None
                and tags_table.c.get("id") is not None
                and tags_table.c.get("name") is not None
            ):
                scene_col = tag_link.c.get("scene_id")
                tag_id_col = tag_link.c.get("tag_id")
                tag_id_target = tags_table.c.get("id")
                tag_name = tags_table.c.get("name")
                tag_stmt = (
                    sa.select(
                        scene_col.label("scene_id"),
                        tag_id_target.label("id"),
                        tag_name.label("name"),
                    )
                    .select_from(tag_link.join(tags_table, tag_id_target == tag_id_col))
                    .where(scene_col.in_(normalized_ids))
                )
                _log.debug("tag link table=%s tags table=%s", getattr(tag_link, 'name', None), getattr(tags_table, 'name', None))
                for row in session.execute(tag_stmt):
                    try:
                        sid = int(row.scene_id)
                        tid = int(row.id)
                    except Exception:
                        continue
                    scene = scenes.get(sid)
                    if scene is None:
                        continue
                    scene["tags"].append({"id": tid, "name": row.name})

            group_link = stash_db.get_first_available_table(
                "scene_groups_scenes",
                "scene_group_scenes",
                "scene_groups_scene",
                "scene_group_map",
                "scene_collections_scenes",
            )
            groups_table = stash_db.get_first_available_table(
                "scene_groups",
                "scene_group",
                "scene_collections",
                "collections",
            )
            if group_link is not None and groups_table is not None:
                scene_col = _pick_column(group_link, "scene_id", "sceneId")
                group_col = _pick_column(group_link, "scene_group_id", "group_id", "collection_id", "series_id")
                target_group_id = _pick_column(groups_table, "id", "scene_group_id", "collection_id", "series_id")
                target_group_name = _pick_column(groups_table, "name", "title")
                if scene_col is not None and group_col is not None and target_group_id is not None and target_group_name is not None:
                    group_stmt = (
                        sa.select(
                            scene_col.label("scene_id"),
                            target_group_id.label("group_id"),
                            target_group_name.label("group_name"),
                        )
                        .select_from(group_link.join(groups_table, target_group_id == group_col))
                        .where(scene_col.in_(normalized_ids))
                    )
                    scene_groups_map: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
                    for row in session.execute(group_stmt):
                        try:
                            sid = int(row.scene_id)
                            gid = int(row.group_id)
                        except Exception:
                            continue
                        scene_groups_map[sid].append({"id": gid, "name": row.group_name})
                    for scene_id, scene in scenes.items():
                        if scene_groups_map.get(scene_id):
                            scene["series"] = scene_groups_map[scene_id]

            # Flexible files table detection (column names vary between Stash versions)
            files_populated = False

            link_table = stash_db.get_first_available_table(
                "scenes_files",
                "scene_files",
                "files_scenes",
                "scene_file_map",
                "file_scene_map",
                required_columns=("scene_id", "file_id"),
            )
            files_table = stash_db.get_stash_table("files", required=False)
            video_files_table = stash_db.get_stash_table("video_files", required=False)
            folders_table = stash_db.get_stash_table("folders", required=False)

            if (
                link_table is not None
                and files_table is not None
                and link_table.c.get("scene_id") is not None
                and link_table.c.get("file_id") is not None
                and files_table.c.get("id") is not None
            ):
                scene_col = link_table.c.get("scene_id")
                link_file_col = link_table.c.get("file_id")
                file_id_col = files_table.c.get("id")
                basename_col = files_table.c.get("basename")
                folder_fk_col = files_table.c.get("parent_folder_id")
                size_col = files_table.c.get("size")
                primary_col = link_table.c.get("primary")

                video_duration_col = video_files_table.c.get("duration") if video_files_table is not None else None
                video_width_col = video_files_table.c.get("width") if video_files_table is not None else None
                video_height_col = video_files_table.c.get("height") if video_files_table is not None else None
                video_file_fk = video_files_table.c.get("file_id") if video_files_table is not None else None

                folder_id_col = folders_table.c.get("id") if folders_table is not None else None
                folder_path_col = folders_table.c.get("path") if folders_table is not None else None

                join_clause = link_table.join(files_table, file_id_col == link_file_col)
                if video_files_table is not None and video_file_fk is not None:
                    join_clause = join_clause.join(
                        video_files_table,
                        video_file_fk == file_id_col,
                        isouter=True,
                    )
                if (
                    folders_table is not None
                    and folder_id_col is not None
                    and folder_fk_col is not None
                ):
                    join_clause = join_clause.join(
                        folders_table,
                        folder_id_col == folder_fk_col,
                        isouter=True,
                    )

                _log.debug(
                    "file link table=%s files=%s video=%s folders=%s",
                    getattr(link_table, "name", None),
                    getattr(files_table, "name", None),
                    getattr(video_files_table, "name", None),
                    getattr(folders_table, "name", None),
                )

                file_stmt = (
                    sa.select(
                        scene_col.label("scene_id"),
                        file_id_col.label("id"),
                        _label_or_literal(primary_col, "is_primary", default=0),
                        _label_or_literal(basename_col, "basename"),
                        _label_or_literal(folder_path_col, "folder_path"),
                        _label_or_literal(size_col, "size"),
                        _label_or_literal(video_duration_col, "duration"),
                        _label_or_literal(video_width_col, "width"),
                        _label_or_literal(video_height_col, "height"),
                    )
                    .select_from(join_clause)
                    .where(scene_col.in_(normalized_ids))
                )

                file_rows = session.execute(file_stmt).all()
                file_ids = [int(row.id) for row in file_rows if row.id is not None]
                fingerprints_map: Dict[int, List[Dict[str, Any]]] = defaultdict(list)

                if file_ids:
                    fingerprint_table = stash_db.get_first_available_table(
                        "files_fingerprints",
                        "fingerprints",
                        "file_fingerprints",
                        "filehash",
                        required_columns=("file_id",),
                    )
                    if fingerprint_table is not None:
                        _log.debug(
                            "fingerprint table detected=%s cols=%s",
                            getattr(fingerprint_table, "name", None),
                            {c.name for c in fingerprint_table.c},
                        )
                        file_id_fk = _pick_column(fingerprint_table, "file_id", "fid", "file")
                        type_col = _pick_column(fingerprint_table, "type", "algorithm", "fingerprint_type")
                        value_col = _pick_column(fingerprint_table, "value", "hash", "fingerprint")
                        if file_id_fk is not None and type_col is not None and value_col is not None:
                            fp_stmt = sa.select(
                                file_id_fk.label("file_id"),
                                type_col.label("type"),
                                value_col.label("value"),
                            ).where(file_id_fk.in_(file_ids))
                            for fp_row in session.execute(fp_stmt):
                                try:
                                    fid = int(fp_row.file_id)
                                except Exception:
                                    continue
                                value = fp_row.value
                                if isinstance(value, (bytes, bytearray)):
                                    value = value.hex()
                                fingerprints_map[fid].append({
                                    "type": fp_row.type,
                                    "value": value,
                                })

                for row in file_rows:
                    scene_ref = scenes.get(int(row.scene_id)) if row.scene_id is not None else None
                    if scene_ref is None:
                        continue
                    try:
                        fid = int(row.id)
                    except Exception:
                        fid = None
                    fingerprints = fingerprints_map.get(fid, []) if fid is not None else []
                    duration_val = None
                    if row.duration is not None:
                        try:
                            duration_val = float(row.duration)
                            if duration_val <= 0:
                                duration_val = None
                        except Exception:
                            duration_val = None
                    folder_path = row.folder_path if hasattr(row, "folder_path") else None
                    basename = row.basename if hasattr(row, "basename") else None
                    if folder_path and basename:
                        file_path = str(Path(str(folder_path)) / str(basename))
                    elif basename:
                        file_path = str(basename)
                    else:
                        file_path = None
                    payload = {
                        "id": fid,
                        "path": file_path,
                        "width": row.width if hasattr(row, "width") else None,
                        "height": row.height if hasattr(row, "height") else None,
                        "duration": duration_val,
                        "size": row.size if hasattr(row, "size") else None,
                        "primary": bool(row.is_primary) if hasattr(row, "is_primary") else None,
                        "fingerprints": fingerprints,
                    }
                    scene_ref["files"].append(
                        {k: v for k, v in payload.items() if v is not None or k == "fingerprints"}
                    )
                    if duration_val and (scene_ref.get("duration") is None or scene_ref["duration"] < duration_val):
                        scene_ref["duration"] = duration_val

                files_populated = True

            return scenes
    except Exception:
        _log.exception("Failed to fetch scenes via direct Stash DB access")
        return {}

    return {}


def fetch_scenes_by_ids(scene_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    ordered_ids: List[int] = [int(sid) for sid in scene_ids if sid is not None]
    if not ordered_ids:
        return {}

    results: Dict[int, Dict[str, Any]] = {}

    try:
        db_results = _fetch_scenes_via_db(ordered_ids)
    except Exception:  # pragma: no cover - defensive fallback
        _log.exception("Direct Stash DB fetch raised unexpectedly; ignoring")
        db_results = {}

    if db_results:
        results.update(db_results)

    # Pure SQL path only: ensure we return entries for every requested id (using stubs if lookup failed)
    normalized_results: Dict[int, Dict[str, Any]] = {}
    for sid in ordered_ids:
        payload = results.get(sid)
        if payload is None:
            payload = _stub_scene(sid)
        normalized_results[sid] = _normalize_scene_payload(dict(payload))
    return normalized_results
