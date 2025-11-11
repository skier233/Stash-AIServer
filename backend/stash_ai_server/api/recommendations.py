from __future__ import annotations
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Dict, Any
from stash_ai_server.recommendations.registry import recommender_registry
from stash_ai_server.recommendations.models import (
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

"""Recommendation endpoints backed by recommender_registry."""


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
    """Apply defaults and basic validation; return (validated_config, warnings)."""
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
    """Run a recommender handler and return validated, paginated results."""
    resolved = recommender_registry.get(payload.recommenderId)
    if not resolved:
        raise HTTPException(status_code=404, detail='Recommender not found')
    definition, handler = resolved
    if payload.context not in definition.contexts:
        raise HTTPException(status_code=400, detail='Recommender not valid for context')
    if definition.needs_seed_scenes and not payload.seedSceneIds:
        raise HTTPException(status_code=400, detail='MISSING_SEED_SCENES')

    seed_ids = (payload.seedSceneIds or [])[:1] if not definition.allows_multi_seed else (payload.seedSceneIds or [])
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
        raw_result = await handler({}, req)
    except Exception as e:
        # Log full traceback for debugging and return a controlled 500 to client
        import traceback as _tb
        _log = __import__('logging').getLogger(__name__)
        _log.exception("recommender execution failed for %s", payload.recommenderId)
        raise HTTPException(status_code=500, detail=f'recommender_execution_failed: {e}')

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
            warnings.append(f'scene[{idx}] validation failed')

    # Pagination handling: trust handler when it provides totals/has_more; otherwise slice here
    offset = max(payload.offset or 0, 0)
    limit = payload.limit or len(validated)
    total_available = handler_total if isinstance(handler_total, int) and handler_total >= len(validated) else len(validated)

    if handler_total is not None or handler_has_more is not None:
        page_slice = validated
        has_more = bool(handler_has_more)
        if handler_has_more is None and handler_total is not None:
            has_more = offset + len(validated) < handler_total
        computed_floor = offset + len(validated)
        total_value = handler_total if handler_total is not None else (offset + len(validated) + (1 if has_more else 0))
        total_value = max(total_value, computed_floor)
        next_offset = (offset + len(validated)) if has_more else None
        meta = {'total': total_value, 'offset': offset, 'limit': limit, 'nextOffset': next_offset, 'hasMore': has_more}
        return RecommendationQueryResponse(recommenderId=payload.recommenderId, scenes=page_slice, meta=meta, warnings=warnings or None)

    end = offset + limit
    page_slice = validated[offset:end]
    has_more = end < len(validated)
    next_offset = end if has_more else None
    computed_floor = offset + len(page_slice)
    total_val = max(total_available, computed_floor)
    meta = {'total': total_val, 'offset': offset, 'limit': limit, 'nextOffset': next_offset, 'hasMore': has_more}
    return RecommendationQueryResponse(recommenderId=payload.recommenderId, scenes=page_slice, meta=meta, warnings=warnings or None)