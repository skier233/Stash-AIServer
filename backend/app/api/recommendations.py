from __future__ import annotations
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import random, time, math
from app.recommendations.registry import autodiscover as _auto_discover_recommenders, recommender_registry
from app.recommendations.models import RecContext, RecommendationRequest as NewRecommendationRequest
from app.utils.stash import fetch_scenes_by_ids, sample_scene_ids

try:
    # Optional dependency: stashapp-tools (lazy usage)
    from stashapp import StashInterface  # type: ignore
except Exception:  # pragma: no cover - optional
    StashInterface = None  # type: ignore

router = APIRouter(prefix='/recommendations', tags=['recommendations'])


class RecommendationContext(BaseModel):
    page: Optional[str] = None
    entityId: Optional[str] = None
    isDetailView: Optional[bool] = None
    selectedIds: List[str] | None = None

class SceneRecommendationsRequest(BaseModel):
    algorithm: str = Field('similarity', description="Algorithm key (similarity|recent|popular|random|by_performer|by_tag)")
    min_score: float = Field(0.0, ge=0, le=1)
    limit: int = Field(100, ge=1, le=500, description="Maximum number of scene IDs to return (frontend caps at 200)")
    seed: int | None = Field(None, description="Optional deterministic seed for random algorithm")
    cursor: Optional[str] = Field(None, description="Opaque paging cursor returned from previous response")
    params: Dict[str, Any] | None = Field(None, description="Algorithm-specific parameters")
    context: RecommendationContext | None = Field(None, description="Frontend page context for contextual recommendations")


class SceneRecommendationsResponse(BaseModel):
    ids: list[int]
    algorithm: str
    total: int
    took_ms: int
    note: str | None = None
    next_cursor: str | None = None


class AlgorithmParam(BaseModel):
    name: str
    type: str = 'string'  # number|string|enum|boolean
    label: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: List[Dict[str, str]] | None = None
    default: Any | None = None

class AlgorithmDefinition(BaseModel):
    name: str
    label: str | None = None
    description: str | None = None
    params: List[AlgorithmParam] | None = None


# Deterministic small corpus for stub (mirrors frontend TEST_SCENE_BASE repeated)
BASE_IDS = [14632, 14586, 14466, 14447]


@router.get('/algorithms', response_model=list[AlgorithmDefinition])
async def list_algorithms():
    """Discovery endpoint used by frontend to build dynamic parameter UI."""
    return [
        AlgorithmDefinition(
            name='similarity', label='Similarity', description='Baseline similarity scorer',
            params=[AlgorithmParam(name='min_score', type='number', min=0, max=1, step=0.05, default=0.5)]
        ),
        AlgorithmDefinition(
            name='recent', label='Recent', description='Most recently added scenes'
        ),
        AlgorithmDefinition(
            name='popular', label='Popular', description='Popularity heuristic (demo)'
        ),
        AlgorithmDefinition(
            name='by_performer', label='By Performer', description='Scenes featuring selected performer',
            params=[AlgorithmParam(name='max_performer_scenes', type='number', min=1, max=500, default=50, step=1)]
        ),
        AlgorithmDefinition(
            name='by_tag', label='By Tag', description='Scenes with a selected tag',
            params=[AlgorithmParam(name='max_tag_scenes', type='number', min=1, max=500, default=50, step=1)]
        )
    ]

# ------------------- New Recommender Endpoints (Design Spec Alignment) -------------------

class RecommenderListResponse(BaseModel):
    context: RecContext
    recommenders: list[dict]
    defaultRecommenderId: str

class RecommendationQueryBody(BaseModel):
    context: RecContext
    recommenderId: str
    config: dict = {}
    seedSceneIds: list[int] | None = None
    limit: int | None = None

class RecommendationQueryResponse(BaseModel):
    recommenderId: str
    scenes: list[dict]
    meta: dict

_recommenders_initialized = False
def _ensure_recommenders():
    global _recommenders_initialized
    if _recommenders_initialized:
        return
    _auto_discover_recommenders()
    # Fallback: if autodiscovery produced none, import known modules directly
    if not recommender_registry.list_for_context(RecContext.global_feed):
        try:
            import importlib
            importlib.import_module('app.recommendations.recommenders.baseline_popularity.popularity')
            importlib.import_module('app.recommendations.recommenders.random_recent.random_recent')
        except Exception as e:  # pragma: no cover - defensive
            print('[recommenders] fallback import error', e, flush=True)
    _recommenders_initialized = True

@router.get('/recommenders', response_model=RecommenderListResponse)
async def list_recommenders(context: RecContext = Query(...)):
    _ensure_recommenders()
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
    _ensure_recommenders()
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
    req = NewRecommendationRequest(
        context=payload.context,
        recommenderId=payload.recommenderId,
        config=payload.config,
        seedSceneIds=seed_ids,
        limit=payload.limit
    )
    scenes = await handler({}, req)  # ctx dict placeholder
    meta = { 'total': len(scenes), 'hasMore': False }
    return RecommendationQueryResponse(recommenderId=payload.recommenderId, scenes=scenes, meta=meta)

# -----------------------------------------------------------------------------------------


def _simulate_base_ids(limit: int) -> list[int]:
    ids: list[int] = []
    while len(ids) < limit:
        ids.extend(BASE_IDS)
    return ids[:limit]


def _query_stash_scenes(context: RecommendationContext | None, algorithm: str, params: Dict[str, Any] | None, limit: int) -> list[int]:
    """Attempt to use stashapp-tools if available; fallback to simulated corpus.

    For demonstration we do not execute real GraphQL complexity; just stub patterns:
      - by_performer: return synthetic id range based on performer id hash
      - by_tag: synthetic id range based on tag id hash
    """
    # If stash interface present you could do something like:
    # si = StashInterface(conn={...}) and then si.find_scenes({...}) collecting IDs.
    ids: list[int] = []
    if algorithm == 'by_performer' and context and context.entityId and context.page == 'performers':
        base = int(context.entityId)
        for i in range(limit):
            ids.append(base * 10 + i)
    elif algorithm == 'by_tag' and context and context.entityId and context.page == 'tags':
        base = int(context.entityId)
        for i in range(limit):
            ids.append(base * 100 + i)
    else:
        ids = _simulate_base_ids(limit)
    return ids


@router.post('/scenes', response_model=SceneRecommendationsResponse)
async def recommend_scenes(payload: SceneRecommendationsRequest = Body(...)):
    start = time.time()
    algo = payload.algorithm.lower()
    limit = min(payload.limit, 200)

    # Paging: interpret cursor as offset (simple demo)
    offset = 0
    if payload.cursor:
        try:
            offset = int(payload.cursor)
        except ValueError:
            offset = 0

    raw_ids: list[int]
    if algo in ('by_performer', 'by_tag'):
        raw_ids = _query_stash_scenes(payload.context, algo, payload.params or {}, limit + offset)
    else:
        raw_ids = _simulate_base_ids(limit + offset)

    # Slice window after offset
    ids = raw_ids[offset: offset + limit]

    # Influence ordering for basic algorithms
    if algo == 'recent':
        ids = list(reversed(ids))
    elif algo == 'popular':
        ids = sorted(ids, key=lambda i: (i % 3, i))
    elif algo == 'random':
        rng = random.Random(payload.seed if payload.seed is not None else random.randint(0, 1_000_000))
        rng.shuffle(ids)
    elif algo not in ('similarity', 'by_performer', 'by_tag'):
        algo = 'similarity'

    # Apply min_score pseudo-filter: cut portion if high
    if payload.min_score > 0.8:
        ids = ids[: max(1, int(len(ids) * 0.6))]

    # Next cursor: only if more data would exist (demo: assume max 1000 synthetic rows)
    next_cursor: str | None = None
    MAX_SYNTH = 1000
    if offset + limit < min(MAX_SYNTH, len(raw_ids)):
        next_cursor = str(offset + limit)

    took_ms = int((time.time() - start) * 1000)
    return SceneRecommendationsResponse(
        ids=ids,
        algorithm=algo,
        total=len(ids),
        took_ms=took_ms,
        note='demo recommendations',
        next_cursor=next_cursor
    )
