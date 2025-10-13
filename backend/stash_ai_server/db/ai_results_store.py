from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import select
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
    model_id: str | None
    version: str | None
    categories: Sequence[str] | None


@dataclass(slots=True)
class StoredSceneRun:
    run_id: int
    completed_at: dt.datetime | None
    input_params: Mapping[str, Any] | None
    aggregates: Mapping[str, float]
    models: Sequence[StoredModelSummary]


def _ensure_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _model_identifier(data: Mapping[str, Any]) -> tuple[str | None, str | None]:
    identifier = data.get("identifier")
    if identifier is None:
        identifier = data.get("model_id")
    if identifier is None:
        identifier = data.get("external_id")
    return _ensure_string(identifier), data.get("name")


def _model_lookup_key(model_id: str | None, model_name: str) -> tuple[str | None, str]:
    return (model_id, model_name)


def _upsert_models(
    session: Session,
    *,
    service: str,
    plugin_name: str | None,
    models: Sequence[Mapping[str, Any]]
) -> dict[tuple[str | None, str], AIModel]:
    mapping: dict[tuple[str | None, str], AIModel] = {}
    if not models:
        return mapping
    for raw in models:
        model_id, name = _model_identifier(raw)
        normalized_name = name or (model_id or "unknown")
        lookup_key = _model_lookup_key(model_id, normalized_name)
        if model_id:
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
        categories = raw.get("categories")
        if isinstance(categories, Sequence) and not isinstance(categories, (str, bytes)):
            categories_list = list(categories)
        else:
            categories_list = None
        version = raw.get("version")
        if version is not None:
            version = str(version)
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
        if instance is None:
            instance = AIModel(
                service=service,
                plugin_name=plugin_name,
                model_id=model_id,
                name=normalized_name,
                version=version,
                model_type=raw.get("type"),
                categories=categories_list,
                extra=payload_extra or None,
            )
            session.add(instance)
            session.flush()
        else:
            instance.plugin_name = plugin_name
            instance.name = normalized_name
            instance.version = version
            instance.model_type = raw.get("type")
            instance.categories = categories_list
            if payload_extra:
                instance.extra = payload_extra
        mapping[lookup_key] = instance
    return mapping


def _assign_run_models(
    session: Session,
    *,
    run: AIModelRun,
    model_records: dict[tuple[str | None, str], AIModel],
    models: Sequence[Mapping[str, Any]],
    input_params: Mapping[str, Any] | None = None,
    status: str = "completed",
) -> tuple[list[AIModelRunModel], dict[str, list[AIModelRunModel]]]:
    linked: list[AIModelRunModel] = []
    category_map: dict[str, list[AIModelRunModel]] = {}
    for raw in models:
        model_id, name = _model_identifier(raw)
        normalized_name = name or (model_id or "unknown")
        lookup_key = _model_lookup_key(model_id, normalized_name)
        instance = model_records.get(lookup_key)
        raw_params = raw.get("input_params")
        if isinstance(raw_params, Mapping):
            stored_params = dict(raw_params)
        else:
            stored_params = dict(input_params) if input_params else None
        record = AIModelRunModel(
            run=run,
            model=instance,
            status=status,
            input_params=stored_params,
        )
        session.add(record)
        session.flush()
        linked.append(record)
        categories = raw.get("categories")
        if isinstance(categories, Sequence) and not isinstance(categories, (str, bytes)):
            for category in categories:
                key = str(category)
                category_map.setdefault(key, []).append(record)
    return linked, category_map


def _match_run_model(
    run_models: Sequence[AIModelRunModel],
    category: str | None,
    category_map: Mapping[str, Sequence[AIModelRunModel]] | None,
) -> AIModelRunModel | None:
    if not run_models:
        return None
    if category and category_map:
        candidates = category_map.get(category)
        if candidates:
            return candidates[0]
    return run_models[0]


def _store_scene_timespans(
    session: Session,
    *,
    run: AIModelRun,
    run_models: Sequence[AIModelRunModel],
    category_map: Mapping[str, Sequence[AIModelRunModel]] | None,
    scene_id: str,
    result: Mapping[str, Any],
    resolve_reference: Callable[[str, str | None], int | None] | None = None,
) -> dict[tuple[str | None, str], float]:
    totals: dict[tuple[str | None, str], float] = {}
    timespans = result.get("timespans") or {}
    for category, tags in timespans.items():
        if not isinstance(tags, Mapping):
            continue
        for label, frames in tags.items():
            if not isinstance(frames, Sequence):
                continue
            label_name = str(label)
            category_name = str(category) if category is not None else None
            run_model = _match_run_model(run_models, category_name, category_map)
            for frame in frames:
                if isinstance(frame, Mapping):
                    start = float(frame.get("start", 0.0))
                    end = frame.get("end")
                    confidence = frame.get("confidence")
                    value_json = {
                        k: v
                        for k, v in frame.items()
                        if k not in {"start", "end", "confidence"}
                    }
                else:
                    # Unexpected shape; skip
                    continue
                end_val = float(end) if end is not None else start
                ts = AIResultTimespan(
                    run=run,
                    run_model=run_model,
                    entity_type="scene",
                    entity_id=str(scene_id),
                    payload_type="tag",
                    label=label_name,
                    start_s=float(start),
                    end_s=end_val,
                    confidence=float(confidence) if confidence is not None else None,
                    value_json=value_json or None,
                    reference_id=resolve_reference(label_name, category_name) if resolve_reference else None,
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
    run_models: Sequence[AIModelRunModel],
    category_map: Mapping[str, Sequence[AIModelRunModel]] | None,
    scene_id: str,
    totals: Mapping[tuple[str | None, str], float],
    resolve_reference: Callable[[str, str | None], int | None] | None = None,
) -> None:
    for (category, label), value in totals.items():
        run_model = _match_run_model(run_models, category, category_map)
        aggregate = AIResultAggregate(
            run=run,
            run_model=run_model,
            entity_type="scene",
            entity_id=str(scene_id),
            payload_type="tag",
            category=category,
            label=label,
            reference_id=resolve_reference(label, category) if resolve_reference else None,
            metric="duration_s",
            value_float=value,
        )
        session.add(aggregate)


def store_scene_run(
    *,
    service: str,
    plugin_name: str | None,
    scene_id: str,
    input_params: Mapping[str, Any] | None,
    result_payload: Mapping[str, Any],
    requested_models: Sequence[Mapping[str, Any]] | None = None,
    label_resolver: Callable[[str, str | None], int | None] | None = None,
) -> int:
    """Persist a completed scene run and its associated timespans/aggregates.

    Returns the run id for reference.
    """

    models_payload = result_payload.get("models") or []
    now = dt.datetime.utcnow()
    with SessionLocal() as session:
        model_records = _upsert_models(
            session,
            service=service,
            plugin_name=plugin_name,
            models=requested_models or models_payload,
        )

        run = AIModelRun(
            service=service,
            plugin_name=plugin_name,
            entity_type="scene",
            entity_id=str(scene_id),
            status="completed",
            input_params=dict(input_params) if input_params else None,
            completed_at=now,
            result_metadata={
                "schema_version": result_payload.get("schema_version"),
                "duration": result_payload.get("duration"),
                "frame_interval": result_payload.get("frame_interval"),
            },
        )
        session.add(run)
        session.flush()

        run_models, category_map = _assign_run_models(
            session,
            run=run,
            model_records=model_records,
            models=models_payload,
            input_params=input_params,
        )

        totals = _store_scene_timespans(
            session,
            run=run,
            run_models=run_models,
            category_map=category_map,
            scene_id=str(scene_id),
            result=result_payload,
            resolve_reference=label_resolver,
        )

        if totals:
            _store_aggregates(
                session,
                run=run,
                run_models=run_models,
                category_map=category_map,
                scene_id=str(scene_id),
                totals=totals,
                resolve_reference=label_resolver,
            )

        session.commit()
        return run.id


async def store_scene_run_async(
    *,
    service: str,
    plugin_name: str | None,
    scene_id: str,
    input_params: Mapping[str, Any] | None,
    result_payload: Mapping[str, Any],
    requested_models: Sequence[Mapping[str, Any]] | None = None,
    label_resolver: Callable[[str, str | None], int | None] | None = None,
) -> int:
    return await asyncio.to_thread(
        store_scene_run,
        service=service,
        plugin_name=plugin_name,
        scene_id=scene_id,
        input_params=input_params,
        result_payload=result_payload,
        requested_models=requested_models,
        label_resolver=label_resolver,
    )


def get_latest_scene_run(
    *,
    service: str,
    scene_id: str,
) -> StoredSceneRun | None:
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
                AIModelRun.entity_id == str(scene_id),
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
            label = agg.label if agg.label is not None else ""
            key = f"{category}:{label}" if category else label
            aggregates[key] = float(agg.value_float or 0.0)

        model_summaries: list[StoredModelSummary] = []
        for run_model in run.models:
            model = run_model.model
            model_summaries.append(
                StoredModelSummary(
                    model_name=(model.name if model else "unknown"),
                    model_type=model.model_type if model else None,
                    model_id=model.model_id if model else None,
                    version=model.version if model else None,
                    categories=tuple(model.categories or []) if model and model.categories else None,
                )
            )

        return StoredSceneRun(
            run_id=run.id,
            completed_at=run.completed_at,
            input_params=run.input_params,
            aggregates=aggregates,
            models=model_summaries,
        )


async def get_latest_scene_run_async(
    *,
    service: str,
    scene_id: str,
) -> StoredSceneRun | None:
    return await asyncio.to_thread(get_latest_scene_run, service=service, scene_id=scene_id)
