from __future__ import annotations
from typing import List, Dict, Any
from app.recommendations.registry import recommender
from app.recommendations.models import RecContext, RecommendationRequest
from app.utils.stash import fetch_scenes_by_tag_paginated

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
    limit = request.limit or 40
    offset = request.offset or 0
    scenes, approx_total, has_more = fetch_scenes_by_tag_paginated(118, offset, limit)
    for idx, sc in enumerate(scenes):
        sc.setdefault('debug_meta', {})
        sc['debug_meta']['rank'] = offset + idx
        sc['debug_meta']['source'] = 'baseline_popularity'
    return {
        'scenes': scenes,
        'total': approx_total,
        'has_more': has_more
    }
