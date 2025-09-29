from app.recommendations.registry import recommender
from app.recommendations.models import RecContext, RecommendationRequest
from app.utils.stash import fetch_scenes_by_tag_paginated
from typing import Dict, Any, List
import random, time

@recommender(
    id='random_recent',
    label='Random Recent',
    description='Shuffled sample approximating recent scenes',
    contexts=[RecContext.global_feed, RecContext.similar_scene],
    config=[
        { 'name':'shuffle_span_s','label':'Shuffle Span (s)','type':'number','default':300,'min':5,'max':3600 },
        { 'name':'overfetch_factor','label':'Overfetch Factor','type':'slider','default':3,'min':1,'max':10,'step':1 },
        { 'name':'recent_window','label':'Recent Window','type':'select','default':'7d','options':[ {'value':'1d','label':'1 Day'}, {'value':'7d','label':'7 Days'}, {'value':'30d','label':'30 Days'} ] },
        { 'name':'dedupe_studio','label':'Dedupe Studios','type':'boolean','default':False },
        { 'name':'debug_label','label':'Debug Label','type':'text','default':'recent mix'},
        { 'name':'exclude_tags','label':'Exclude Tags','type':'tags','default':[] },
        { 'name':'pin_performers','label':'Pin Performers','type':'performers','default':[] },
        { 'name':'random_mode','label':'Random Mode','type':'enum','default':'time_seed','options':[ {'value':'time_seed','label':'Time Seed'}, {'value':'pure','label':'Pure Random'} ] },
    ],
    supports_pagination=False,
    exposes_scores=False
)
async def random_recent(ctx: Dict[str, Any], request: RecommendationRequest):
    cfg = request.config or {}
    limit = request.limit or 40
    offset = request.offset or 0
    shuffle_span_s = cfg.get('shuffle_span_s', 300)
    overfetch_factor = max(1, int(cfg.get('overfetch_factor', 3)))
    recent_window = cfg.get('recent_window', '7d')
    dedupe_studio = cfg.get('dedupe_studio', False)
    exclude_tags = set(cfg.get('exclude_tags') or [])
    pin_performers = set(cfg.get('pin_performers') or [])
    random_mode = cfg.get('random_mode', 'time_seed')
    debug_label = cfg.get('debug_label')
    fetch_limit = limit * overfetch_factor
    scenes, approx_total, has_more = fetch_scenes_by_tag_paginated(932, offset, fetch_limit)
    if exclude_tags:
        def _allowed(sc):
            tag_ids = {t.get('id') for t in sc.get('tags', []) if isinstance(t, dict)}
            return not (tag_ids & exclude_tags)
        scenes = [s for s in scenes if _allowed(s)]
    if random_mode == 'pure':
        random.shuffle(scenes)
    else:
        seed_bucket = int(time.time() // max(1, shuffle_span_s))
        random.seed(seed_bucket + offset)
        random.shuffle(scenes)
    if pin_performers:
        pinned: List[Dict[str, Any]] = []
        others: List[Dict[str, Any]] = []
        for sc in scenes:
            perf_ids = {p.get('id') for p in sc.get('performers', []) if isinstance(p, dict)}
            (pinned if perf_ids & pin_performers else others).append(sc)
        scenes = pinned + others
    if dedupe_studio:
        seen_studios = set(); deduped: List[Dict[str, Any]] = []
        for sc in scenes:
            stud = sc.get('studio', {}).get('id') if isinstance(sc.get('studio'), dict) else None
            if stud and stud in seen_studios: continue
            if stud: seen_studios.add(stud)
            deduped.append(sc)
        scenes = deduped
    if len(scenes) > limit:
        scenes = scenes[:limit]
    seed_bucket = int(time.time() // max(1, shuffle_span_s)) if random_mode != 'pure' else None
    for idx, sc in enumerate(scenes):
        dm = sc.setdefault('debug_meta', {})
        dm['rank'] = offset + idx
        dm['source'] = 'random_recent'
        dm['random_mode'] = random_mode
        if seed_bucket is not None: dm['seed_bucket'] = seed_bucket
        dm['overfetch_factor'] = overfetch_factor
        dm['recent_window'] = recent_window
        if dedupe_studio: dm['deduped_studio'] = True
        if exclude_tags: dm['exclude_tags'] = list(exclude_tags)
        if pin_performers: dm['pin_performers'] = list(pin_performers)
        if debug_label: dm['label'] = debug_label
    return {'scenes': scenes, 'total': approx_total, 'has_more': has_more}
