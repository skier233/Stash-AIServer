from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.ai_results import (
    AIModel,
    AIModelRun,
    AIModelRunModel,
    AIResultAggregate,
    AIResultTimespan,
)


@dataclass(slots=True)
class StoredModelSummary:
    model_name: str
    model_type: str | None
    model_id: int | None
    version: float | None
    categories: Sequence[str] | None
    extra: Mapping[str, Any] | None
    frame_interval: float | None
    threshold: float | None


@dataclass(slots=True)
class StoredSceneRun:
    run_id: int
    completed_at: dt.datetime | None
    input_params: Mapping[str, Any] | None
    aggregates: Mapping[str, float]
    models: Sequence[StoredModelSummary]


# TODO: This may not be needed
def _extract_frame_interval_from_run_instance(run: AIModelRun) -> float | None:
    if run is None:
        return None
    metadata = run.result_metadata or {}
    interval = _safe_float(metadata.get("frame_interval")) if isinstance(metadata, Mapping) else None
    if interval is not None:
        return interval
    return _extract_float(run.input_params, "frame_interval")


SceneTimespanBuckets = dict[str | None, dict[str | None, list[dict[str, Any]]]]


def _prepare_input_params(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    normalized = _normalize_null_strings(value)
    if isinstance(normalized, Mapping):
        return dict(normalized)
    return None


def _clean_category_list(value: Any) -> list[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    normalized = _normalize_null_strings(list(value))
    if not isinstance(normalized, Iterable) or isinstance(normalized, (str, bytes, Mapping)):
        return None
    cleaned: list[str] = []
    for item in normalized:
        if item is None:
            continue
        text = item.strip() if isinstance(item, str) else str(item).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"null", "none"}:
            continue
        cleaned.append(text)
    return cleaned or None


def _clean_category_value(value: Any) -> str | None:
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"null", "none"}:
        return None
    return text


def get_scene_timespans(
    *,
    service: str,
    scene_id: int,
) -> SceneTimespanBuckets | None:
    """
    Retrieve ALL timespans for a scene across all runs, ordered for merge processing.

    Returns a mapping of timespans by category and label where each entry is a
    pre-ordered list of timespan dicts (start, end, confidence). Frame interval
    is deliberately not returned — callers should not rely on it.
    """
    scene_id_int = _ensure_int(scene_id)
    if scene_id_int is None:
        raise ValueError("scene_id must be an integer")
    
    with SessionLocal() as session:
        # Get all timespans for this scene, ordered for merge processing
        timespans_stmt = (
            select(AIResultTimespan)
            .join(AIModelRun, AIResultTimespan.run_id == AIModelRun.id)
            .where(
                AIModelRun.service == service,
                AIModelRun.entity_type == "scene",
                AIModelRun.entity_id == scene_id_int,
                AIResultTimespan.payload_type == "tag",
            )
            .order_by(
                AIResultTimespan.category,
                AIResultTimespan.str_value,
                AIResultTimespan.start_s
            )
        )
        ordered_timespans = session.execute(timespans_stmt).scalars().all()
        
        if not ordered_timespans:
            return None

        timespan_map: SceneTimespanBuckets = {}
        for ts in ordered_timespans:
            raw_payload = ts.value_json if isinstance(ts.value_json, Mapping) else {}
            payload_dict = dict(raw_payload) if raw_payload else {}
            confidence = _safe_float(payload_dict.get("confidence"))
            
            # The DB stores tag_id in value_id
            tag_id = ts.value_id if hasattr(ts, "value_id") and ts.value_id is not None else None
            if tag_id is None:
                continue
                
            category_key = ts.category
            label_key = str(tag_id)  # Use tag_id as the key
            
            bucket = timespan_map.setdefault(category_key, {})
            entry = {
                "start": float(ts.start_s or 0.0),
                "end": float(ts.end_s if ts.end_s is not None else ts.start_s),
                "confidence": confidence,
            }
            bucket.setdefault(label_key, []).append(entry)

        return timespan_map


async def get_scene_timespans_async(
    *,
    service: str,
    scene_id: int,
) -> SceneTimespanBuckets | None:
    return await asyncio.to_thread(get_scene_timespans, service=service, scene_id=scene_id)


def get_scene_tag_totals(
    *,
    service: str,
    scene_id: int,
) -> dict[int, float]:
    scene_id_int = _ensure_int(scene_id)
    if scene_id_int is None:
        raise ValueError("scene_id must be an integer")

    with SessionLocal() as session:
        stmt = (
            select(
                AIResultAggregate.value_id,
                AIResultAggregate.value_float,
            )
            .join(AIModelRun, AIResultAggregate.run_id == AIModelRun.id)
            .where(
                AIModelRun.service == service,
                AIModelRun.entity_type == "scene",
                AIModelRun.entity_id == scene_id_int,
                AIResultAggregate.payload_type == "tag",
                AIResultAggregate.metric == "duration_s",
                AIResultAggregate.value_id.isnot(None),
            )
        )

        totals: dict[int, float] = {}
        for tag_id, value in session.execute(stmt):
            tag_int = int(tag_id)
            totals[tag_int] = totals.get(tag_int, 0.0) + float(value or 0.0)

        return totals


async def get_scene_tag_totals_async(
    *,
    service: str,
    scene_id: int,
) -> dict[int, float]:
    return await asyncio.to_thread(
        get_scene_tag_totals,
        service=service,
        scene_id=scene_id,
    )


def _ensure_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


from stash_ai_server.utils.string_utils import normalize_null_strings as _normalize_null_strings


def _model_identifier(data: Mapping[str, Any]) -> tuple[int | None, str | None]:
    identifier = data.get("identifier")
    if identifier is None:
        identifier = data.get("model_id")
    if identifier is None:
        identifier = data.get("external_id")
    return _ensure_int(identifier), data.get("name")


def _model_lookup_key(model_id: int | None, model_name: str) -> tuple[int | None, str]:
    return (model_id, model_name)


def _upsert_models(
    session: Session,
    *,
    service: str,
    plugin_name: str | None,
    models: Sequence[Mapping[str, Any]]
) -> dict[tuple[int | None, str], AIModel]:
    mapping: dict[tuple[int | None, str], AIModel] = {}
    if not models:
        return mapping
    for raw in models:
        model_id, name = _model_identifier(raw)
        normalized_name = name or (str(model_id) if model_id is not None else "unknown")
        lookup_key = _model_lookup_key(model_id, normalized_name)
        if model_id is not None:
            stmt = select(AIModel).where(
                AIModel.service == service,
                AIModel.model_id == model_id,
                AIModel.name == normalized_name,
            )
        else:
            stmt = select(AIModel).where(
                AIModel.service == service,
                AIModel.model_id.is_(None),
                AIModel.name == normalized_name,
            )
        instance = session.execute(stmt).scalar_one_or_none()
        categories_list = _clean_category_list(raw.get("categories"))
        version_value = raw.get("version")
        try:
            version = float(version_value) if version_value is not None else None
        except (TypeError, ValueError):
            version = None
        payload_extra = {
            k: v
            for k, v in raw.items()
            if k
            not in {
                "identifier",
                "model_id",
                "external_id",
                "name",
                "version",
                "type",
                "categories",
            }
        }
        # normalize string 'null' values (recursively) to None to avoid wasted storage
        payload_extra = _normalize_null_strings(payload_extra)
        if isinstance(payload_extra, Mapping):
            payload_extra = {k: v for k, v in payload_extra.items()}
        else:
            payload_extra = None
        if instance is None:
            instance = AIModel(
                service=service,
                plugin_name=plugin_name,
                model_id=model_id,
                name=normalized_name,
                version=version,
                model_type=raw.get("type"),
                categories=categories_list,
                extra=payload_extra,
            )
            session.add(instance)
            session.flush()
        else:
            instance.plugin_name = plugin_name
            instance.name = normalized_name
            instance.version = version
            instance.model_type = raw.get("type")
            instance.categories = categories_list
            instance.extra = payload_extra
        mapping[lookup_key] = instance
    return mapping


def _assign_run_models(
    session: Session,
    *,
    run: AIModelRun,
    model_records: dict[tuple[int | None, str], AIModel],
    models: Sequence[Mapping[str, Any]],
    input_params: Mapping[str, Any] | None = None,
    frame_interval: float | None = None,
) -> list[AIModelRunModel]:
    linked: list[AIModelRunModel] = []
    for raw in models:
        model_id, name = _model_identifier(raw)
        normalized_name = name or (str(model_id) if model_id is not None else "unknown")
        lookup_key = _model_lookup_key(model_id, normalized_name)
        instance = model_records.get(lookup_key)
        raw_params = _prepare_input_params(raw.get("input_params"))
        if raw_params is not None:
            stored_params = raw_params
        else:
            stored_params = dict(input_params) if input_params else None
        record = AIModelRunModel(
            run=run,
            model=instance,
            input_params=stored_params,
            frame_interval=float(frame_interval) if frame_interval is not None else None,
        )
        session.add(record)
        session.flush()
        linked.append(record)
    return linked


def _extract_float(params: Any, key: str) -> float | None:
    if not isinstance(params, Mapping):
        return None
    value = params.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_model_history(
    session: Session,
    *,
    service: str,
    entity_type: str,
    entity_id: int,
) -> list[StoredModelSummary]:
    stmt = (
        select(AIModelRunModel)
        .options(
            selectinload(AIModelRunModel.model),
            selectinload(AIModelRunModel.run),
        )
        .join(AIModelRun, AIModelRunModel.run_id == AIModelRun.id)
        .where(
            AIModelRun.service == service,
            AIModelRun.entity_type == entity_type,
            AIModelRun.entity_id == entity_id,
        )
    )
    run_models = session.scalars(stmt).all()

    aggregated: dict[tuple[int | None, str], StoredModelSummary] = {}
    for run_model in run_models:
        model = run_model.model
        if model is None:
            continue

        key = _model_lookup_key(model.model_id, model.name)
        categories_tuple = tuple(model.categories or []) if model.categories else None

        frame_interval_value = run_model.frame_interval
        if frame_interval_value is None:
            frame_interval_value = _extract_float(run_model.input_params, "frame_interval")
            if frame_interval_value is None and run_model.run is not None:
                frame_interval_value = _extract_float(run_model.run.input_params, "frame_interval")

        threshold_value = _extract_float(run_model.input_params, "threshold")
        if threshold_value is None and run_model.run is not None:
            threshold_value = _extract_float(run_model.run.input_params, "threshold")

        summary = aggregated.get(key)
        if summary is None:
            aggregated[key] = StoredModelSummary(
                model_name=model.name,
                model_type=model.model_type,
                model_id=model.model_id,
                version=model.version,
                categories=categories_tuple,
                extra=model.extra,
                frame_interval=frame_interval_value,
                threshold=threshold_value,
            )
            continue

        if summary.model_type is None and model.model_type is not None:
            summary.model_type = model.model_type
        if summary.model_id is None and model.model_id is not None:
            summary.model_id = model.model_id
        if model.version is not None and (summary.version is None or model.version > summary.version):
            summary.version = model.version
        if summary.extra is None and model.extra:
            summary.extra = model.extra
        if categories_tuple:
            existing_categories = set(summary.categories or [])
            existing_categories.update(categories_tuple)
            summary.categories = tuple(sorted(existing_categories))
        if summary.frame_interval is None and frame_interval_value is not None:
            summary.frame_interval = frame_interval_value
        if summary.threshold is None and threshold_value is not None:
            summary.threshold = threshold_value

    def _sort_key(item: StoredModelSummary) -> tuple[str, int]:
        name_key = item.model_name.lower() if item.model_name else ""
        id_key = item.model_id if item.model_id is not None else -1
        return (name_key, id_key)

    return sorted(aggregated.values(), key=_sort_key)


def _store_scene_timespans(
    session: Session,
    *,
    run: AIModelRun,
    scene_id: int,
    result: Mapping[str, Any],
    frame_interval: float | None,
    resolve_reference: Callable[[str, str | None], int | None] | None = None,
) -> dict[tuple[str | None, str], float]:
    totals: dict[tuple[str | None, str], float] = {}
    timespans = result.get("timespans") or {}
    interval = float(frame_interval) if frame_interval is not None else 2.0
    for category, tags in timespans.items():
        if not isinstance(tags, Mapping):
            continue
        for label, frames in tags.items():
            if not isinstance(frames, Sequence):
                continue
            label_name = str(label)
            category_name = _clean_category_value(category)
            for frame in frames:
                if isinstance(frame, Mapping):
                    start = float(frame.get("start", 0.0))
                    end = frame.get("end")
                    value_json = {k: v for k, v in frame.items() if k not in {"start", "end"}}
                    # normalize 'null' strings recursively
                    value_json = _normalize_null_strings(value_json)
                else:
                    # Unexpected shape; skip
                    continue
                resolved_id = resolve_reference(label_name, category_name) if resolve_reference else None
                end_val = (float(end) if end is not None else float(start)) + interval
                ts = AIResultTimespan(
                    run=run,
                    entity_type="scene",
                    entity_id=scene_id,
                    payload_type="tag",
                    category=category_name,
                    str_value=None,
                    value_id=resolved_id,
                    start_s=float(start),
                    end_s=end_val,
                    value_json=value_json if value_json else None,
                )
                session.add(ts)
                span = max(0.0, end_val - float(start))
                key = (category_name, label_name)
                totals[key] = totals.get(key, 0.0) + span
    return totals


def _store_aggregates(
    session: Session,
    *,
    run: AIModelRun,
    scene_id: int,
    totals: Mapping[tuple[str | None, str], float],
    resolve_reference: Callable[[str, str | None], int | None] | None = None,
) -> None:
    for (category, label), value in totals.items():
        label_name = str(label)
        resolved_id = resolve_reference(label_name, category) if resolve_reference else None
        if resolved_id is None:
            continue
        aggregate = AIResultAggregate(
            run=run,
            entity_type="scene",
            entity_id=scene_id,
            payload_type="tag",
            category=category,
            str_value=None,
            value_id=resolved_id,
            metric="duration_s",
            value_float=float(value),
        )
        session.add(aggregate)


def store_scene_run(
    *,
    service: str,
    plugin_name: str | None,
    scene_id: int,
    input_params: Mapping[str, Any] | None,
    result_payload: Mapping[str, Any],
    requested_models: Sequence[Mapping[str, Any]] | None = None,
    resolve_reference: Callable[[str, str | None], int | None] | None = None,
) -> int:
    """Persist a completed scene run and its associated timespans/aggregates.

    Returns the run id for reference.
    """

    scene_id_int = _ensure_int(scene_id)
    if scene_id_int is None:
        raise ValueError("scene_id must be an integer")

    models_payload = result_payload.get("models") or []
    now = dt.datetime.utcnow()
    normalized_input_params = _prepare_input_params(input_params)
    with SessionLocal() as session:
        model_records = _upsert_models(
            session,
            service=service,
            plugin_name=plugin_name,
            models=requested_models or models_payload,
        )

        frame_interval_value = result_payload.get("frame_interval")
        try:
            frame_interval_float: float | None = (
                float(frame_interval_value) if frame_interval_value is not None else None
            )
        except (TypeError, ValueError):
            frame_interval_float = None
        if frame_interval_float is None and normalized_input_params:
            try:
                frame_interval_float = float(normalized_input_params.get("frame_interval"))  # type: ignore[arg-type]
            except (TypeError, ValueError, AttributeError):
                frame_interval_float = None

        raw_metadata = {
            "schema_version": result_payload.get("schema_version"),
            "duration": result_payload.get("duration"),
            "frame_interval": result_payload.get("frame_interval"),
        }
        normalized_metadata = _normalize_null_strings(raw_metadata)
        metadata_dict = dict(normalized_metadata) if isinstance(normalized_metadata, Mapping) else None

        run = AIModelRun(
            service=service,
            plugin_name=plugin_name,
            entity_type="scene",
            entity_id=scene_id_int,
            status="completed",
            input_params=normalized_input_params,
            completed_at=now,
            result_metadata=metadata_dict,
        )
        session.add(run)
        session.flush()

        _assign_run_models(
            session,
            run=run,
            model_records=model_records,
            models=models_payload,
            input_params=normalized_input_params,
            frame_interval=frame_interval_float,
        )

        totals = _store_scene_timespans(
            session,
            run=run,
            scene_id=scene_id_int,
            result=result_payload,
            frame_interval=frame_interval_float,
            resolve_reference=resolve_reference,
        )

        if totals:
            _store_aggregates(
                session,
                run=run,
                scene_id=scene_id_int,
                totals=totals,
                resolve_reference=resolve_reference,
            )

        session.commit()
        return run.id


async def store_scene_run_async(
    *,
    service: str,
    plugin_name: str | None,
    scene_id: int,
    input_params: Mapping[str, Any] | None,
    result_payload: Mapping[str, Any],
    requested_models: Sequence[Mapping[str, Any]] | None = None,
    resolve_reference: Callable[[str, str | None], int | None] | None = None,
) -> int:
    return await asyncio.to_thread(
        store_scene_run,
        service=service,
        plugin_name=plugin_name,
        scene_id=scene_id,
        input_params=input_params,
        result_payload=result_payload,
        requested_models=requested_models,
        resolve_reference=resolve_reference,
    )


def store_image_run(
    *,
    service: str,
    plugin_name: str | None,
    image_id: int,
    tag_records: Mapping[str, Sequence[int]] | None,
    input_params: Mapping[str, Any] | None = None,
    requested_models: Sequence[Mapping[str, Any]] | None = None,
) -> int:
    image_id_int = _ensure_int(image_id)
    if image_id_int is None:
        raise ValueError("image_id must be an integer")

    normalized_tag_records: dict[str | None, Sequence[int]] = {}
    categories_to_clear: list[str] = []
    clear_null_category = False
    for raw_category, ids in (tag_records or {}).items():
        cleaned_category = _clean_category_value(raw_category)
        if cleaned_category is None:
            clear_null_category = True
        else:
            categories_to_clear.append(cleaned_category)
        normalized_tag_records[cleaned_category] = ids

    now = dt.datetime.now(dt.timezone.utc)
    normalized_input_params = _prepare_input_params(input_params)
    with SessionLocal() as session:
        model_records = _upsert_models(
            session,
            service=service,
            plugin_name=plugin_name,
            models=requested_models or [],
        )

        run = AIModelRun(
            service=service,
            plugin_name=plugin_name,
            entity_type="image",
            entity_id=image_id_int,
            status="completed",
            input_params=normalized_input_params,
            completed_at=now,
        )
        session.add(run)
        session.flush()

        if categories_to_clear or clear_null_category:
            stale_run_ids = (
                select(AIModelRun.id)
                .where(
                    AIModelRun.service == service,
                    AIModelRun.entity_type == "image",
                    AIModelRun.entity_id == image_id_int,
                    AIModelRun.id != run.id,
                )
            )
            if categories_to_clear:
                session.execute(
                    delete(AIResultAggregate).where(
                        AIResultAggregate.run_id.in_(stale_run_ids),
                        AIResultAggregate.category.in_(categories_to_clear),
                    )
                )
            if clear_null_category:
                session.execute(
                    delete(AIResultAggregate).where(
                        AIResultAggregate.run_id.in_(stale_run_ids),
                        AIResultAggregate.category.is_(None),
                    )
                )

        if requested_models:
            _assign_run_models(
                session,
                run=run,
                model_records=model_records,
                models=requested_models,
                input_params=normalized_input_params,
                frame_interval=None,
            )

        for category_value, tag_ids in normalized_tag_records.items():
            for tag_id in tag_ids:
                aggregate = AIResultAggregate(
                    run=run,
                    entity_type="image",
                    entity_id=image_id_int,
                    payload_type="tag",
                    category=category_value,
                    str_value=None,
                    value_id=tag_id,
                    metric="presence",
                    value_float=None,
                )
                session.add(aggregate)

        session.commit()
        return run.id


async def store_image_run_async(
    *,
    service: str,
    plugin_name: str | None,
    image_id: int,
    tag_records: Mapping[str | None, Sequence[int]] | None,
    input_params: Mapping[str, Any] | None = None,
    requested_models: Sequence[Mapping[str, Any]] | None = None,
) -> int:
    return await asyncio.to_thread(
        store_image_run,
        service=service,
        plugin_name=plugin_name,
        image_id=image_id,
        tag_records=tag_records,
        input_params=input_params,
        requested_models=requested_models,
    )


def get_scene_model_history(
    *,
    service: str,
    scene_id: int,
) -> Sequence[StoredModelSummary]:
    scene_id_int = _ensure_int(scene_id)
    if scene_id_int is None:
        raise ValueError("scene_id must be an integer")

    with SessionLocal() as session:
        model_summaries = _collect_model_history(
            session,
            service=service,
            entity_type="scene",
            entity_id=scene_id_int,
        )
        return tuple(model_summaries)


def get_image_model_history(
    *,
    service: str,
    image_id: int,
) -> Sequence[StoredModelSummary]:
    image_id_int = _ensure_int(image_id)
    if image_id_int is None:
        raise ValueError("image_id must be an integer")

    with SessionLocal() as session:
        model_summaries = _collect_model_history(
            session,
            service=service,
            entity_type="image",
            entity_id=image_id_int,
        )
        return tuple(model_summaries)


def get_latest_scene_run(
    *,
    service: str,
    scene_id: int,
) -> StoredSceneRun | None:
    scene_id_int = _ensure_int(scene_id)
    if scene_id_int is None:
        raise ValueError("scene_id must be an integer")
    with SessionLocal() as session:
        stmt = (
            select(AIModelRun)
            .options(
                selectinload(AIModelRun.models).selectinload(AIModelRunModel.model),
                selectinload(AIModelRun.aggregates),
            )
            .where(
                AIModelRun.service == service,
                AIModelRun.entity_type == "scene",
                AIModelRun.entity_id == scene_id_int,
            )
            .order_by(AIModelRun.completed_at.desc().nullslast(), AIModelRun.id.desc())
            .limit(1)
        )
        run = session.execute(stmt).scalar_one_or_none()
        if run is None:
            return None

        aggregates: dict[str, float] = {}
        for agg in run.aggregates:
            if agg.metric != "duration_s":
                continue
            category = agg.category if agg.category is not None else ""
            # Prefer numeric tag_id for stable joins; fall back to str_value when missing
            if getattr(agg, "value_id", None) is not None:
                label_key = str(agg.value_id)
            else:
                label_key = agg.str_value if getattr(agg, "str_value", None) is not None else ""
            key = f"{category}:{label_key}" if category else label_key
            aggregates[key] = float(agg.value_float or 0.0)

        model_summaries = _collect_model_history(
            session,
            service=service,
            entity_type="scene",
            entity_id=scene_id_int,
        )

        return StoredSceneRun(
            run_id=run.id,
            completed_at=run.completed_at,
            input_params=run.input_params,
            aggregates=aggregates,
            models=model_summaries,
        )


def get_image_tag_ids(
    *,
    service: str,
    image_id: int,
) -> list[int]:
    image_id_int = _ensure_int(image_id)
    if image_id_int is None:
        raise ValueError("image_id must be an integer")

    with SessionLocal() as session:
        stmt = (
            select(AIResultAggregate.value_id)
            .join(AIModelRun, AIResultAggregate.run_id == AIModelRun.id)
            .where(
                AIModelRun.service == service,
                AIModelRun.entity_type == "image",
                AIModelRun.entity_id == image_id_int,
                AIResultAggregate.payload_type == "tag",
                AIResultAggregate.metric == "presence",
                AIResultAggregate.value_id.isnot(None),
            )
        )

        tag_ids: list[int] = []
        seen: set[int] = set()
        for (value_id,) in session.execute(stmt):
            tag_int = int(value_id)
            if tag_int in seen:
                continue
            seen.add(tag_int)
            tag_ids.append(tag_int)

        return tag_ids


async def get_image_tag_ids_async(
    *,
    service: str,
    image_id: int,
) -> list[int]:
    return await asyncio.to_thread(get_image_tag_ids, service=service, image_id=image_id)


def purge_scene_categories(
    *,
    service: str,
    scene_id: int,
    categories: Sequence[str],
    exclude_run_id: int | None = None,
) -> None:
    if not categories:
        return
    category_set = {c for c in categories if c}
    if not category_set:
        return

    scene_id_int = _ensure_int(scene_id)
    if scene_id_int is None:
        raise ValueError("scene_id must be an integer")

    with SessionLocal() as session:
        # build subquery of run ids for this scene/service
        run_ids_subq = select(AIModelRun.id).where(
            AIModelRun.service == service,
            AIModelRun.entity_type == "scene",
            AIModelRun.entity_id == scene_id_int,
        )
        if exclude_run_id is not None:
            run_ids_subq = run_ids_subq.where(AIModelRun.id != exclude_run_id)

        # delete aggregates for matching run ids and categories
        agg_del = delete(AIResultAggregate).where(
            AIResultAggregate.run_id.in_(run_ids_subq),
            AIResultAggregate.category.in_(category_set),
        )
        session.execute(agg_del)

        # delete timespans for matching run ids and categories
        ts_del = delete(AIResultTimespan).where(
            AIResultTimespan.run_id.in_(run_ids_subq),
            AIResultTimespan.category.in_(category_set),
        )
        session.execute(ts_del)

        session.commit()


async def get_latest_scene_run_async(
    *,
    service: str,
    scene_id: int,
) -> StoredSceneRun | None:
    return await asyncio.to_thread(get_latest_scene_run, service=service, scene_id=scene_id)


async def get_scene_model_history_async(
    *,
    service: str,
    scene_id: int,
) -> Sequence[StoredModelSummary]:
    return await asyncio.to_thread(get_scene_model_history, service=service, scene_id=scene_id)


async def get_image_model_history_async(
    *,
    service: str,
    image_id: int,
) -> Sequence[StoredModelSummary]:
    return await asyncio.to_thread(get_image_model_history, service=service, image_id=image_id)
