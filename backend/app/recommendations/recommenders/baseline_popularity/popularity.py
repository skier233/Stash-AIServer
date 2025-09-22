from __future__ import annotations
from typing import List, Dict, Any
from app.recommendations.registry import recommender
from app.recommendations.models import RecContext, RecommendationRequest
from app.utils.stash import fetch_scenes_by_tag

@recommender(
    id='baseline_popularity',
    label='Baseline Popularity',
    description='Deterministic pseudo-popularity ordering over sample scene set',
    contexts=[RecContext.global_feed],
    config=[],
    supports_pagination=False,
    exposes_scores=False
)
async def baseline_popularity(ctx: Dict[str, Any], request: RecommendationRequest):
    # Deterministic ordering: treat lower (id % 7) as more "popular" then by id
    limit = request.limit or 80
    # Tag-based selection: tag id 118 per user instruction
    scenes = fetch_scenes_by_tag(118, limit)
    # Attach lightweight debug field so UI/network diff is visible
    for idx, sc in enumerate(scenes):
        sc.setdefault('debug_meta', {})
        sc['debug_meta']['rank'] = idx
        sc['debug_meta']['source'] = 'baseline_popularity'
    return scenes
