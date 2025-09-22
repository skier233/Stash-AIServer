from __future__ import annotations
from typing import Dict, Any
import random, time
from app.recommendations.registry import recommender
from app.recommendations.models import RecContext, RecommendationRequest
from app.utils.stash import fetch_scenes_by_tag

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
    limit = request.limit or 80
    # Pull scenes from tag 932 and then shuffle for variability
    scenes = fetch_scenes_by_tag(932, limit * 3)  # over-fetch then downsample
    random.seed(int(time.time() // 5))  # changes every 5s
    random.shuffle(scenes)
    scenes = scenes[:limit]
    for idx, sc in enumerate(scenes):
        sc.setdefault('debug_meta', {})
        sc['debug_meta']['rank'] = idx
        sc['debug_meta']['source'] = 'random_recent'
    return scenes
