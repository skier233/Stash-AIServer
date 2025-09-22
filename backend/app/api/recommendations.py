from __future__ import annotations
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Dict, Any
from app.recommendations.registry import recommender_registry
from app.recommendations.models import (
    RecContext,
    RecommendationRequest as NewRecommendationRequest,
    SceneModel,
    RecommenderDefinition,
    RecommenderConfigField,
)

router = APIRouter(prefix='/recommendations', tags=['recommendations'])


class RecommendationContext(BaseModel):
    page: Optional[str] = None
    entityId: Optional[str] = None
    isDetailView: Optional[bool] = None
    selectedIds: List[str] | None = None

"""Legacy algorithm request/response models removed (frontend now uses hydrated recommender API)."""


"""Removed legacy algorithm discovery models and stub ID corpus."""


# ------------------- Recommender Endpoints (Design Spec Alignment) -------------------

class RecommenderListResponse(BaseModel):
    context: RecContext
    recommenders: list[dict]
    defaultRecommenderId: str

class RecommendationQueryBody(BaseModel):
    context: RecContext
    recommenderId: str
    config: dict = {}
    seedSceneIds: list[int] | None = None
    # Client supplies limit (page size) and optional offset (start index). If offset omitted, defaults to 0.
    limit: int | None = None
    offset: int | None = 0

class RecommendationQueryResponse(BaseModel):
    recommenderId: str
    scenes: list[dict]
    # meta structure (design add): { total, offset, limit, nextOffset, hasMore }
    meta: dict
    warnings: list[str] | None = None

"""Recommenders are initialized at FastAPI startup (see main._init_recommenders)."""

def _validate_config(defn: RecommenderDefinition, raw: dict) -> tuple[dict, list[str]]:
    """Apply defaults + type/constraint validation for config fields.

    Currently lenient: returns (validated_config, warnings). Future enhancement
    could raise instead. Only fields declared in defn.config are retained; extras
    are ignored (with a warning).
    """
    if not defn.config:
        return raw or {}, []
    spec: dict[str, RecommenderConfigField] = {c.name: c for c in defn.config}
    out: dict = {}
    warnings: list[str] = []
    incoming = raw or {}
    for name, field in spec.items():
        if name in incoming:
            val = incoming[name]
        else:
            val = field.default
        # Basic numeric constraint enforcement
        if field.type in ('number','slider') and val is not None:
            try:
                fval = float(val)
                if field.min is not None and fval < field.min:
                    warnings.append(f'config.{name} below min; clamped')
                    fval = field.min
                if field.max is not None and fval > field.max:
                    warnings.append(f'config.{name} above max; clamped')
                    fval = field.max
                if field.type == 'number':
                    # Keep numeric type
                    val = fval
                else:
                    val = fval
            except (TypeError, ValueError):
                warnings.append(f'config.{name} invalid numeric; using default')
                val = field.default
        if field.required and val is None:
            warnings.append(f'config.{name} required but missing')
        out[name] = val
    # Extras detection
    for k in incoming.keys():
        if k not in spec:
            warnings.append(f'config.{k} ignored (undeclared)')
    return out, warnings

@router.get('/recommenders', response_model=RecommenderListResponse)
async def list_recommenders(context: RecContext = Query(...)):
    defs = recommender_registry.list_for_context(context)
    if not defs:
        return RecommenderListResponse(context=context, recommenders=[], defaultRecommenderId='')
    default_id = defs[0].id
    return RecommenderListResponse(
        context=context,
        recommenders=[d.dict() for d in defs],
        defaultRecommenderId=default_id
    )

@router.post('/query', response_model=RecommendationQueryResponse)
async def query_recommendations(payload: RecommendationQueryBody = Body(...)):
    resolved = recommender_registry.get(payload.recommenderId)
    if not resolved:
        raise HTTPException(status_code=404, detail='Recommender not found')
    definition, handler = resolved
    if payload.context not in definition.contexts:
        raise HTTPException(status_code=400, detail='Recommender not valid for context')
    if definition.needs_seed_scenes and not payload.seedSceneIds:
        raise HTTPException(status_code=400, detail='MISSING_SEED_SCENES')
    # Multi-seed restriction
    seed_ids = payload.seedSceneIds or []
    if not definition.allows_multi_seed and len(seed_ids) > 1:
        seed_ids = seed_ids[:1]
    # Config validation (lenient warnings)
    validated_config, cfg_warnings = _validate_config(definition, payload.config or {})
    req = NewRecommendationRequest(
        context=payload.context,
        recommenderId=payload.recommenderId,
        config=validated_config,
        seedSceneIds=seed_ids,
        limit=payload.limit,
        offset=payload.offset or 0,
    )
    warnings: list[str] = []
    warnings.extend(cfg_warnings)
    try:
        raw_result = await handler({}, req)  # ctx dict placeholder
    except Exception as e:  # bubble error gracefully
        raise HTTPException(status_code=500, detail=f'recommender_execution_failed: {e}')
    # Handler may return either a list[scene] OR a dict with keys: scenes, total, has_more
    if isinstance(raw_result, dict):
        raw_scenes = raw_result.get('scenes', [])
        handler_total = raw_result.get('total')
        handler_has_more = raw_result.get('has_more')
    else:
        raw_scenes = raw_result  # type: ignore
        handler_total = None
        handler_has_more = None
    validated: list[dict] = []
    for idx, sc in enumerate(raw_scenes):  # type: ignore
        try:
            model = SceneModel.parse_obj(sc)
            validated.append(model.dict())
        except ValidationError as ve:
            warnings.append(f'scene[{idx}] validation failed: {ve.errors()[0].get("loc")}')
    # Pagination slicing (offset-based). If recommender does not support pagination we still slice deterministically in handler order.
    offset = payload.offset or 0
    if offset < 0:
        offset = 0
    limit = payload.limit or len(validated)
    total_available = handler_total if isinstance(handler_total, int) and handler_total >= len(validated) else len(validated)
    # If handler already handled pagination (indicated by handler_total or handler_has_more), trust it and do not re-slice
    if handler_total is not None or handler_has_more is not None:
        page_slice = validated
        has_more = bool(handler_has_more)
        # Derive has_more if only total provided
        if handler_has_more is None and handler_total is not None:
            has_more = offset + len(validated) < handler_total
        # Guarantee floor: total cannot be less than the highest index we have displayed
        computed_floor = offset + len(validated)
        total_value = handler_total if handler_total is not None else (offset + len(validated) + (1 if has_more else 0))
        if total_value < computed_floor:
            total_value = computed_floor
        next_offset = (offset + len(validated)) if has_more else None
        meta = {
            'total': total_value,
            'offset': offset,
            'limit': limit,
            'nextOffset': next_offset,
            'hasMore': has_more,
        }
        return RecommendationQueryResponse(recommenderId=payload.recommenderId, scenes=page_slice, meta=meta, warnings=warnings or None)
    # Fallback: slice in API layer
    end = offset + limit
    page_slice = validated[offset:end]
    has_more = end < len(validated)
    next_offset = end if has_more else None
    computed_floor = offset + len(page_slice)
    total_val = total_available
    if total_val < computed_floor:
        total_val = computed_floor
    meta = {
        'total': total_val,
        'offset': offset,
        'limit': limit,
        'nextOffset': next_offset,
        'hasMore': has_more,
    }
    return RecommendationQueryResponse(recommenderId=payload.recommenderId, scenes=page_slice, meta=meta, warnings=warnings or None)