from __future__ import annotations
from typing import Dict, Any
import random, time
from app.recommendations.registry import recommender
from app.recommendations.models import RecContext, RecommendationRequest
from app.utils.stash import fetch_scenes_by_tag_paginated

@recommender(
    id='random_recent',
    label='Random Recent',
    description='Shuffled sample approximating recent scenes',
    contexts=[RecContext.global_feed, RecContext.similar_scene],
    config=[],
    supports_pagination=False,
    exposes_scores=False
)
async def random_recent(ctx: Dict[str, Any], request: RecommendationRequest):
    limit = request.limit or 40
    offset = request.offset or 0
    # Fetch a window slightly larger than requested to keep randomness within page but stable across quick refetches
    fetch_limit = limit
    scenes, approx_total, has_more = fetch_scenes_by_tag_paginated(932, offset, fetch_limit)
    # Shuffle deterministically within a 5-second bucket so pagination stable during quick navigation
    seed_bucket = int(time.time() // 5)
    random.seed(seed_bucket + offset)
    random.shuffle(scenes)
    for idx, sc in enumerate(scenes):
        sc.setdefault('debug_meta', {})
        sc['debug_meta']['rank'] = offset + idx
        sc['debug_meta']['seed_bucket'] = seed_bucket
        sc['debug_meta']['source'] = 'random_recent'
    return {
        'scenes': scenes,
        'total': approx_total,
        'has_more': has_more
    }
