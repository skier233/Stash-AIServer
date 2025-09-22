from __future__ import annotations
from fastapi import APIRouter, Body
from pydantic import BaseModel, Field
import random, time

router = APIRouter(prefix='/recommendations', tags=['recommendations'])


class SceneRecommendationsRequest(BaseModel):
    algorithm: str = Field('similarity', description="Algorithm key (similarity|recent|popular|random)")
    min_score: float = Field(0.0, ge=0, le=1)
    limit: int = Field(100, ge=1, le=500, description="Maximum number of scene IDs to return")
    seed: int | None = Field(None, description="Optional deterministic seed for random algorithm")


class SceneRecommendationsResponse(BaseModel):
    ids: list[int]
    algorithm: str
    total: int
    took_ms: int
    note: str | None = None


# Deterministic small corpus for stub (mirrors frontend TEST_SCENE_BASE repeated)
BASE_IDS = [14632, 14586, 14466, 14447]


@router.post('/scenes', response_model=SceneRecommendationsResponse)
async def recommend_scenes(payload: SceneRecommendationsRequest = Body(...)):
    start = time.time()
    algo = payload.algorithm.lower()
    # Expand base IDs until >= limit (cyclical repeat) – placeholder for real query logic.
    ids: list[int] = []
    while len(ids) < payload.limit:
        ids.extend(BASE_IDS)
    ids = ids[:payload.limit]

    # Pretend algorithms influence ordering
    if algo == 'recent':
        # Reverse order
        ids = list(reversed(ids))
    elif algo == 'popular':
        # Sort by simulated popularity (stable shuffle based on id hash)
        ids = sorted(ids, key=lambda i: (i % 3, i))
    elif algo == 'random':
        rng = random.Random(payload.seed if payload.seed is not None else random.randint(0, 1_000_000))
        rng.shuffle(ids)
    else:
        algo = 'similarity'
        # For similarity keep as‑is but we could later weight by score and filter min_score

    # Apply min_score filter stub: simulate dropping last N if threshold high
    if payload.min_score > 0.8:
        ids = ids[: max(1, int(len(ids) * 0.6))]

    took_ms = int((time.time() - start) * 1000)
    return SceneRecommendationsResponse(ids=ids, algorithm=algo, total=len(ids), took_ms=took_ms, note='stubbed recommendations')
