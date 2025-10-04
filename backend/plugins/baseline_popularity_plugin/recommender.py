from stash_ai_server.recommendations.registry import recommender
from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.utils.stash import fetch_scenes_by_tag_paginated
from typing import Dict, Any

@recommender(
    id='baseline_popularity',
    label='Baseline Popularity',
    description='Deterministic pseudo-popularity ordering over sample scene set',
    contexts=[RecContext.global_feed, RecContext.similar_scene],
    config=[
        { 'name':'min_score','label':'Min Score','type':'number','default':0,'min':0,'max':100 },
        { 'name':'rank_window','label':'Rank Window','type':'slider','default':50,'min':10,'max':200,'step':10 },
        { 'name':'ordering','label':'Ordering','type':'select','default':'pop','options': [ {'value':'pop','label':'Popularity'}, {'value':'id_desc','label':'ID Desc'}, {'value':'id_asc','label':'ID Asc'} ] },
        { 'name':'include_studio','label':'Include Studio Name','type':'boolean','default':True },
        { 'name':'note','label':'Annotate','type':'text','default':'baseline run'},
        { 'name':'search_query','label':'Search','type':'search','default':'','help':'Sample search style input' },
        { 'name':'focus_tags','label':'Focus Tags','type':'tags','default':[],'tag_combination':'and', 'constraint_types':['presence','duration'], 'allowed_combination_modes':['or', 'and'] },
        { 'name':'filter_tags','label':'Filter Tags','type':'tags','default':[],'tag_combination':'or', 'constraint_types':['overlap','importance'], 'allowed_combination_modes':['and'] },
        { 'name':'boost_performers','label':'Boost Performers','type':'performers','default':[] },
        { 'name':'scoring_mode','label':'Scoring Mode','type':'enum','default':'simple','options':[ {'value':'simple','label':'Simple'}, {'value':'weighted','label':'Weighted'} ] },
    ],
    supports_pagination=False,
    exposes_scores=False
)
async def baseline_popularity(ctx: Dict[str, Any], request: RecommendationRequest):
    cfg = request.config or {}
    limit = request.limit or 40
    offset = request.offset or 0
    scenes, approx_total, has_more = fetch_scenes_by_tag_paginated(118, offset, limit)
    min_score = cfg.get('min_score')
    if isinstance(min_score, (int, float)) and min_score > 0:
        scenes = [s for s in scenes if (s.get('rating100') or 0) >= min_score]
    ordering = cfg.get('ordering') or 'pop'
    if ordering == 'id_desc':
        scenes.sort(key=lambda s: s.get('id', 0), reverse=True)
    elif ordering == 'id_asc':
        scenes.sort(key=lambda s: s.get('id', 0))
    focus_tags = set(cfg.get('focus_tags') or [])
    boost_performers = set(cfg.get('boost_performers') or [])
    if focus_tags or boost_performers:
        def _score(scene: Dict[str, Any]):
            tag_ids = {t.get('id') for t in scene.get('tags', []) if isinstance(t, dict)}
            performer_ids = {p.get('id') for p in scene.get('performers', []) if isinstance(p, dict)}
            return (len(tag_ids & focus_tags) * 2) + len(performer_ids & boost_performers)
        scenes.sort(key=_score, reverse=True)
    scoring_mode = cfg.get('scoring_mode') or 'simple'
    if scoring_mode == 'weighted':
        for sc in scenes:
            base = (sc.get('rating100') or 0) / 100.0
            tag_ids = {t.get('id') for t in sc.get('tags', []) if isinstance(t, dict)}
            performer_ids = {p.get('id') for p in sc.get('performers', []) if isinstance(p, dict)}
            tag_bonus = 0.1 * len(tag_ids & focus_tags)
            perf_bonus = 0.05 * len(performer_ids & boost_performers)
            sc['score'] = round(min(1.0, base + tag_bonus + perf_bonus), 4)
    include_studio = cfg.get('include_studio', True)
    if not include_studio:
        for sc in scenes:
            sc['studio'] = None
    note = cfg.get('note')
    rank_window = cfg.get('rank_window')
    for idx, sc in enumerate(scenes):
        dm = sc.setdefault('debug_meta', {})
        dm['rank'] = offset + idx
        dm['source'] = 'baseline_popularity'
        if note: dm['note'] = note
        if focus_tags: dm['focus_tag_hit'] = bool(set(t.get('id') for t in sc.get('tags', [])) & focus_tags)
        if boost_performers: dm['performer_boost_hit'] = bool(set(p.get('id') for p in sc.get('performers', [])) & boost_performers)
        if rank_window: dm['rank_window'] = rank_window
        dm['ordering'] = ordering
        dm['scoring_mode'] = scoring_mode
    return {'scenes': scenes,'total': approx_total,'has_more': has_more}
