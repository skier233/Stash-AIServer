# Recommendation System Design (Draft)

> Status: DRAFT – iteration 3 (renamed Provider -> Recommender, multi-seed + frontend-only persistence; added folder structure).

## Goals

Provide an extensible recommendation subsystem that can surface scene recommendations across multiple contexts:
1. Global feed ("For You") – list of N scenes user will likely enjoy.
2. Similar-to-one-or-more-scenes – recommendations seeded by one OR multiple reference scenes (multi‑seed enabled).
3. (Future) Least recommended / pruning candidates.

Key requirements:
- Pluggable recommenders ("engines") discovered dynamically (decorator pattern similar to existing action registry).
- Each recommender declares: id, human label, contexts supported, config schema, capability flags (supportsPagination, supportsLimitOverride, needsSeedScenes, allowsMultiSeed, etc.).
- Backend single endpoint to list recommenders for a given context + current defaults.
- Backend endpoint to execute a recommendation request returning a batch of fully-hydrated scene objects (UI should not re-query per scene).
- UI dynamically renders recommender-specific config controls using schema (field types: select, number, slider, boolean, tag-selector, performer-selector (future), text).
- Frontend-only persistence (v1): per-recommender config stored in localStorage (namespaced). No server persistence initially.
- Support remote recommenders later (recommender points to server_url) without changing contract.

## Terminology
- Recommender / Engine: Implementation producing ranked scenes.
- Context: Where the recommendation is displayed. Enum: `global_feed`, `similar_scene`, `prune_candidates` (extensible).
- Recommendation Request: Input describing context + active recommender + recommender config + optional seed scene IDs + limit.

## High-Level Flow
1. UI loads page -> `GET /api/v1/recommendations/recommenders?context=global_feed`.
2. Backend returns list of recommender metadata + default config + constraints.
3. User chooses recommender (or default) + adjusts config. UI persists config (localStorage namespaced by recommender id + context).
4. UI requests recommendations: `POST /api/v1/recommendations/query`.
5. Backend resolves recommender implementation (registry), validates config, invokes handler (async if needed).
6. Recommender returns ranked list of normalized scene objects + optional scores / meta.
7. UI renders scenes directly (no per-id GraphQL fetch).

## Backend Architecture

### Folder Structure
```
backend/app/recommendations/
  __init__.py
  registry.py           # decorator + registry for recommenders
  models.py / types.py  # RecContext, RecommenderConfigField, RecommenderDefinition, RecommendationRequest
  orchestrator.py       # (optional) shared execution / validation helpers
  recommenders/         # each recommender grouped logically
    __init__.py
    baseline_popularity/
      __init__.py
      popularity.py     # @recommender decorated function(s)
    random_weighted/
      __init__.py
      random_weighted.py
    tag_overlap/
      __init__.py
      tag_overlap.py
    remote_proxy/
      __init__.py
      remote_proxy.py   # adapter for remote HTTP recommender
```
Rationale: isolates core registry from individual recommender logic; each recommender can expand with helper modules, caches, or model artifacts (embeddings files) inside its own folder.

### Recommender Decorator & Models

```python
# backend/app/recommendations/registry.py
from __future__ import annotations
from typing import Callable, Any, Dict, List, Awaitable
from enum import Enum
from pydantic import BaseModel

class RecContext(str, Enum):
    global_feed = "global_feed"
    similar_scene = "similar_scene"  # supports single OR multi-seed
    prune_candidates = "prune_candidates"

class RecommenderConfigField(BaseModel):
    name: str
    label: str
    type: str  # number | slider | select | boolean | text | tags | performers | enum
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: List[dict] | None = None   # [{"value": ..., "label": ...}]
    help: str | None = None

class RecommenderDefinition(BaseModel):
    id: str
    label: str
    description: str = ""
    contexts: List[RecContext]
    config: List[RecommenderConfigField] = []
    # capability flags
    supports_limit_override: bool = True
    supports_pagination: bool = True
    needs_seed_scenes: bool = False  # if True and context is similar_scene must have >=1 seed
    allows_multi_seed: bool = True   # can accept multiple seed scenes
    exposes_scores: bool = True      # scoring explanation availability

RecommendationResult = List[dict]  # normalized scenes (SceneModel contract)

class RecommendationRequest(BaseModel):
    context: RecContext
    recommenderId: str
    config: dict
    seedSceneIds: List[int] | None = None
    limit: int | None = None

RecommenderHandler = Callable[[dict, RecommendationRequest], RecommendationResult | Awaitable[RecommendationResult]]

def recommender(*, id: str, label: str, contexts: List[RecContext], description: str = "", config: List[RecommenderConfigField] | None = None, **caps):
    """Decorator to register a recommender."""
    def wrapper(fn):
        definition = RecommenderDefinition(id=id, label=label, contexts=contexts, description=description, config=config or [], **caps)
        setattr(fn, "_recommender_definition", definition)
        return fn
    return wrapper
```

#### Example Recommender (Tag Overlap Similarity)

```python
# backend/app/recommendations/recommenders/tag_overlap/tag_overlap.py
from ..registry import recommender, RecContext, RecommenderConfigField, RecommendationRequest
from typing import List, Dict

@recommender(
    id="similarity_tag_overlap",
    label="Tag Overlap Similarity",
    description="Ranks scenes by Jaccard overlap of tags with one or more seed scenes.",
    contexts=[RecContext.similar_scene],
    config=[
        RecommenderConfigField(
            name="min_tag_overlap", label="Min Overlap", type="slider",
            min=0.1, max=1.0, step=0.05, default=0.2,
            help="Discard candidates with tag overlap below this threshold"
        ),
        RecommenderConfigField(
            name="sample_pool", label="Sample Pool Size", type="number",
            min=50, max=5000, step=50, default=400
        ),
    ],
    needs_seed_scenes=True,
    allows_multi_seed=True,
    supports_pagination=False,
    exposes_scores=True,
)
async def tag_overlap_recommender(ctx: dict, request: RecommendationRequest):
    seed_ids = request.seedSceneIds or []
    seed_tags: Dict[int, set[int]] = await load_tags_for_scenes(seed_ids)  # pseudo helper
    union_seed_tags: set[int] = set().union(*seed_tags.values()) if seed_tags else set()

    cfg = request.config
    min_overlap = cfg.get("min_tag_overlap", 0.2)
    sample_pool = int(cfg.get("sample_pool", 400))

    candidates = await sample_candidate_scenes(limit=sample_pool, exclude_ids=seed_ids)  # pseudo helper

    scored: List[dict] = []
    for scene in candidates:
        tags = set(scene.get("tag_ids", []))
        if not tags or not union_seed_tags:
            continue
        jaccard = len(tags & union_seed_tags) / len(tags | union_seed_tags)
        if jaccard < min_overlap:
            continue
        scored.append({
            "id": scene["id"],
            "title": scene.get("title"),
            "paths": {"screenshot": scene.get("screenshot_path")},
            "tags": scene.get("tags", []),
            "performers": scene.get("performers", []),
            "score": jaccard,
        })

    scored.sort(key=lambda s: s["score"], reverse=True)
    limit = request.limit or 80
    return scored[:limit]
```

### Registry Responsibilities
```
RecommenderRegistry:
  - register(definition, handler)
  - list_for_context(context) -> List[RecommenderDefinition]
  - get(id) -> (definition, handler)
```
Collection mirrors existing action/service registries (module scan for decorated functions under `recommendations/recommenders/*`).

### API Endpoints
```
GET  /api/v1/recommendations/recommenders?context=global_feed
  Response: {
    "context": "global_feed",
    "recommenders": [ { RecommenderDefinition + serverDefaults } ],
    "defaultRecommenderId": "baseline_popularity"
  }

POST /api/v1/recommendations/query
  Body (multi-seed aware): {
    "context": "global_feed" | "similar_scene" | "prune_candidates",
    "recommenderId": "similarity_tag_overlap",
    "config": { "min_tag_overlap": 0.3, "sample_pool": 500 },
    "seedSceneIds": [12345, 67890],  # required if recommender.needs_seed_scenes
    "limit": 80                      # optional if recommender supports override
  }
  Response: {
    "recommenderId": "similarity_tag_overlap",
    "scenes": [ SceneModel... ],
    "meta": { "total": 80, "hasMore": false, "scores": { sceneId: 0.92 } }
  }
```
Backward Compatibility: Temporary aliases (`/recommendations/providers`, `providerId`) can be maintained to avoid breaking existing UI until migration complete. Server normalizes legacy fields to recommender equivalents.

Pagination (v1): single page result; UI paginates client-side. Future: cursor tokens + incremental fetch.

### SceneModel Contract
```
{
  "id": int,
  "title": str | None,
  "rating100": int | None,
  "studio": {"id": int, "name": str} | None,
  "paths": {"screenshot": str | None, "preview": str | None},
  "performers": [ {"id": int, "name": str} ],
  "tags": [ {"id": int, "name": str} ],
  "files": [ {"width": int, "height": int, "duration": int, "size": int} ],
  "score": float | None
}
```
Missing optional fields are tolerated.

### Recommender Implementation Patterns
- Popularity / recency blend (DB weight)
- Random weighted by rating or tag frequency
- Tag-overlap similarity (example)
- Embedding KNN (future vectors table)
- Remote HTTP recommender (adapter)

### Validation & Errors
- Config validated via dynamic Pydantic model built from `RecommenderConfigField` definitions.
- Error shape: `{ "error": { "code": "INVALID_CONFIG", "message": "...", "field": "min_tag_overlap" } }`.
- Missing seeds: `MISSING_SEED_SCENES`.

## Frontend Integration

### Fetch Recommenders
`GET /api/v1/recommendations/recommenders?context=global_feed`

Populate dropdown from response `recommenders`. On recommender change, rebuild dynamic config form from `config` array.

### Persistence Strategy (Frontend-Only v1)
- localStorage key pattern: `ai.recs.{context}.{recommenderId}.{field}`
- On switch: start from default then apply overrides
- Reset button clears keys for active recommender+context
- Future: `/api/v1/recommendations/prefs` for per-user persistence.

### Request Flow
1. Build payload with context/recommenderId/config + (seedSceneIds[] if needed)
2. POST query
3. Replace in-memory `scenes` list
4. Client pagination / zoom over returned list

### Dynamic Form Rendering Sketch
```tsx
fields.map(f => {
  switch (f.type) {
    case 'number': return <NumberInput key={f.name} {...commonProps} defaultValue={values[f.name]} />
    case 'slider': return <Slider key={f.name} min={f.min} max={f.max} step={f.step} value={values[f.name]} />
    case 'select': return <Select key={f.name} options={f.options} value={values[f.name]} />
    case 'boolean': return <Toggle key={f.name} checked={values[f.name]} />
    default: return <TextInput key={f.name} value={values[f.name] ?? ''} />
  }
})
```

### Multi-Seed Behavior
- If `needs_seed_scenes` and none provided -> 400 `MISSING_SEED_SCENES`.
- If `allows_multi_seed` is false and >1 seeds provided -> truncate to first; add optional warning.
- UI can start with single selection but always send array (length 0 or 1). Future: multi-select widget.

## Extensibility & Future Work
- Cursor pagination (`nextCursor`)
- Score overlay badges (if `exposes_scores`)
- Hybrid recommender (blend child recommenders)
- Remote recommender adapter + caching
- Preference feedback loop (accepted / skipped logging)
- Pruning context specialized recommenders (duplicates, low engagement)
- Explanation objects per scene (`reasons`: [ {type, weight, label} ])

## Open Questions
- Global deduping of already watched scenes – global filter vs recommender responsibility?
- Do we need server-side user defaults soon?
- Security concerns for remote recommender endpoints & config sanitization.

## Implementation Phases
1. Registry + decorator + baseline recommenders (popularity, random, tag-overlap)
2. Endpoints + frontend dynamic dropdown + basic field rendering
3. Scene hydration (remove per-id GraphQL) + client pagination
4. Similar-scene context integration (single seed) then multi-seed UI
5. Advanced field types (tags / performers) + remote recommender support
6. Score overlays, pruning context, cursor pagination

## Migration Note
Legacy term "provider" was renamed to "recommender" for clarity. Temporary compatibility layer may map old endpoint/field names (providerId -> recommenderId) until UI updated everywhere.

---
End of draft v3.

# Recommendation System Design (Draft)

> Status: DRAFT – iteration 2 (incorporates multi-seed + frontend-only persistence clarifications).

## Goals

Provide an extensible recommendation subsystem that can surface scene recommendations across multiple contexts:
1. Global feed ("For You") – list of N scenes user will likely enjoy.
2. Similar-to-one-or-more-scenes – recommendations seeded by one OR multiple reference scenes (multi‑seed enabled).
3. (Future) Least recommended / pruning candidates.

Key requirements:
- Pluggable providers ("engines") discovered dynamically (decorator pattern similar to existing action registry).
- Each provider declares: id, human label, contexts supported, config schema, capability flags (supportsPagination, supportsLimitOverride, needsSeedScene, allowsMultiSeed, etc.).
- Backend single endpoint to list providers for a given context + current defaults.
- Backend endpoint to execute a recommendation request returning a batch of fully-hydrated scene objects (UI should not re-query per scene).
- UI dynamically renders provider-specific config controls using schema (field types: select, number, slider, boolean, tag-selector, performer-selector (future), text).
- Frontend-only persistence (v1): per-provider config stored in localStorage (namespaced). No server persistence initially.
- Support remote providers later (provider points to server_url similar to ServiceBase) without changing contract.

## Terminology
- Provider / Engine: Implementation producing ranked scenes.
- Context: Where the recommendation is displayed. Enum: `global_feed`, `similar_scene`, `prune_candidates` (extensible).
- Recommendation Request: Input describing context + active provider + provider config + optional seed scene IDs (plural) + limit.

## High-Level Flow
1. UI loads page (global feed route) -> calls `GET /api/v1/recommendations/providers?context=global_feed`.
2. Backend returns list of provider metadata + default config + any backend-derived constraints.
3. User chooses provider (or default) + adjusts config. UI persists config (localStorage key namespaced by provider id + context).
4. UI requests recommendations: `POST /api/v1/recommendations/query` with payload.
5. Backend resolves provider implementation (registry), validates config against provider schema, invokes provider (async if needed).
6. Provider returns ranked list of scene models (already normalized to format expected by existing SceneCard) and optional scores/explanations.
7. UI renders scenes directly – no per-scene GraphQL fetch.

## Backend Architecture

### Provider Decorator & Models
Mirrors existing action/service registry patterns.

```python
# backend/app/recommendations/registry.py
from __future__ import annotations
from typing import Callable, Any, Dict, List, Awaitable
from enum import Enum
from pydantic import BaseModel

class RecContext(str, Enum):
    global_feed = "global_feed"
    similar_scene = "similar_scene"  # supports single OR multi-seed
    prune_candidates = "prune_candidates"

class ProviderConfigField(BaseModel):
    name: str
    label: str
    type: str  # number | slider | select | boolean | text | tags | performers | enum
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: List[dict] | None = None   # [{"value": ..., "label": ...}]
    help: str | None = None

class ProviderDefinition(BaseModel):
    id: str
    label: str
    description: str = ""
    contexts: List[RecContext]
    config: List[ProviderConfigField] = []
    # capability flags
    supports_limit_override: bool = True
    supports_pagination: bool = True
    needs_seed_scene: bool = False  # if True and context is similar_scene must have >=1 seed
    allows_multi_seed: bool = True  # providers can set False if they only handle single seed
    # scoring explanation availability
    exposes_scores: bool = True

RecommendationResult = List[dict]  # each dict is normalized scene (see SceneModel contract below)

class RecommendationRequest(BaseModel):
    context: RecContext
    providerId: str
    config: dict
    seedSceneIds: List[int] | None = None
    limit: int | None = None

ProviderHandler = Callable[[dict, RecommendationRequest], RecommendationResult | Awaitable[RecommendationResult]]

def provider(*, id: str, label: str, contexts: List[RecContext], description: str = "", config: List[ProviderConfigField] | None = None, **caps):
    """Decorator to register a recommendation provider."""
    def wrapper(fn):
        definition = ProviderDefinition(id=id, label=label, contexts=contexts, description=description, config=config or [], **caps)
        setattr(fn, "_rec_provider_definition", definition)
        return fn
    return wrapper
```

#### Richer Example Provider
Illustrates config fields, multi-seed handling, and async logic.

```python
# backend/app/recommendations/providers/tag_overlap.py
from .registry import provider, RecContext, ProviderConfigField, RecommendationRequest
from typing import List, Dict

@provider(
    id="similarity_tag_overlap",
    label="Tag Overlap Similarity",
    description="Ranks scenes by Jaccard overlap of tags with one or more seed scenes.",
    contexts=[RecContext.similar_scene],
    config=[
        ProviderConfigField(
            name="min_tag_overlap",
            label="Min Overlap",
            type="slider",
            min=0.1,
            max=1.0,
            step=0.05,
            default=0.2,
            help="Discard candidates with tag overlap below this threshold"
        ),
        ProviderConfigField(
            name="sample_pool",
            label="Sample Pool Size",
            type="number",
            min=50,
            max=5000,
            step=50,
            default=400
        ),
    ],
    needs_seed_scene=True,
    allows_multi_seed=True,
    supports_pagination=False,
    exposes_scores=True,
)
async def tag_overlap_provider(ctx: dict, request: RecommendationRequest):
    seed_ids = request.seedSceneIds or []
    seed_tags: Dict[int, set[int]] = await load_tags_for_scenes(seed_ids)  # pseudo helper
    union_seed_tags: set[int] = set().union(*seed_tags.values()) if seed_tags else set()

    cfg = request.config
    min_overlap = cfg.get("min_tag_overlap", 0.2)
    sample_pool = int(cfg.get("sample_pool", 400))

    candidates = await sample_candidate_scenes(limit=sample_pool, exclude_ids=seed_ids)  # pseudo helper

    scored: List[dict] = []
    for scene in candidates:
        tags = set(scene.get("tag_ids", []))
        if not tags or not union_seed_tags:
            continue
        jaccard = len(tags & union_seed_tags) / len(tags | union_seed_tags)
        if jaccard < min_overlap:
            continue
        scored.append({
            "id": scene["id"],
            "title": scene.get("title"),
            "paths": {"screenshot": scene.get("screenshot_path")},
            "tags": scene.get("tags", []),
            "performers": scene.get("performers", []),
            "score": jaccard,
        })

    scored.sort(key=lambda s: s["score"], reverse=True)
    limit = request.limit or 80
    return scored[:limit]
```

### Registry
```
RecommendationRegistry:
  - register(definition, handler)
  - list_for_context(context) -> List[ProviderDefinition]
  - get(id) -> (definition, handler)
```
Collection logic mirrors existing action/service registries (scan module path for decorated functions).

### API Endpoints
```
GET  /api/v1/recommendations/providers?context=global_feed
  Response: {
    "context": "global_feed",
    "providers": [ { ProviderDefinition + serverDefaults } ],
    "defaultProviderId": "baseline_popularity"
  }

POST /api/v1/recommendations/query
  Body (multi-seed aware): {
    "context": "global_feed" | "similar_scene" | "prune_candidates",
    "providerId": "similarity_v1",
    "config": { "minScore": 0.5, "sample": 200 },
    "seedSceneIds": [12345, 67890],      # optional; required if provider.needs_seed_scene. If provider.allows_multi_seed == false only first element honored.
    "limit": 80                          # optional if provider supports override
  }
  Response: {
    "providerId": "similarity_v1",
    "scenes": [ SceneModel... ],
    "meta": { "total": 80, "hasMore": false, "scores": { sceneId: 0.92 } }
  }
```
Backward Compatibility: For a short window we may accept legacy `seedSceneId` (int) and internally normalize to `seedSceneIds=[id]`.

Pagination (v1): single page result; UI paginates client-side. Future: cursor tokens + incremental fetch if `hasMore` true.

### SceneModel Contract
Return objects shaped for existing `SceneCard`. Minimal baseline keys:
```
{
  "id": int,
  "title": str | None,
  "rating100": int | None,
  "studio": {"id": int, "name": str} | None,
  "paths": {"screenshot": str | None, "preview": str | None},
  "performers": [ {"id": int, "name": str} ],
  "tags": [ {"id": int, "name": str} ],
  "files": [ {"width": int, "height": int, "duration": int, "size": int} ],
  "score": float | None  # optional provider score
}
```
Providers may omit optional fields; UI tolerates absence.

### Provider Implementation Patterns
- Popularity / recency blend (DB query ordering by weighted score)
- Random weighted by rating or tag frequency
- Tag-overlap similarity (example above)
- Embedding nearest-neighbor search (future; vectors table)
- Remote HTTP provider (adapter pattern) translating contract

### Validation & Errors
- Config validated by dynamic Pydantic model derived from ProviderConfigField definitions.
- Standard error shape: `{ "error": { "code": "INVALID_CONFIG", "message": "...", "field": "minScore" } }`.
- Missing seeds error: code `MISSING_SEED_SCENES`.

## Frontend Integration

### Fetch Providers
On mount (and when context changes):
`GET /api/v1/recommendations/providers?context=global_feed` (or other context)

Populate dropdown using returned providers; show `label`. When provider changes, rebuild dynamic config form from `config` array. Field types map to components:
- number / slider -> numeric input / range slider
- select / enum -> `<select>`
- boolean -> styled checkbox
- tags / performers (future) -> async multi-select

### Persistence Strategy (Frontend-Only v1)
- localStorage key pattern: `ai.recs.{context}.{providerId}.{field}`
- On provider switch: start from provider default then apply stored overrides
- Reset button clears keys for active provider+context
- Rationale: zero backend complexity, aligns with existing plugin patterns. Future: `/api/v1/recommendations/prefs` for per-user sync if needed.

### Request Flow
When user clicks Refresh or provider/config changes:
1. Build payload with context/providerId/config + (seedSceneIds[] if context=similar_scene and provider requires seeds)
2. POST query endpoint
3. Replace `scenes` state with returned list (no per-scene fetch)
4. Use existing client pagination & zoom over this list

### Dynamic Form Rendering Sketch
```tsx
fields.map(f => {
  switch (f.type) {
    case 'number': return <NumberInput key={f.name} {...commonProps} defaultValue={values[f.name]} />
    case 'slider': return <Slider key={f.name} min={f.min} max={f.max} step={f.step} value={values[f.name]} />
    case 'select': return <Select key={f.name} options={f.options} value={values[f.name]} />
    case 'boolean': return <Toggle key={f.name} checked={values[f.name]} />
    default: return <TextInput key={f.name} value={values[f.name] ?? ''} />
  }
})
```

### Multi-Seed Behavior
- If `needs_seed_scene` and no seeds -> 400 with `MISSING_SEED_SCENES`.
- If `allows_multi_seed` is false and >1 seeds provided -> server truncates to first; may add `meta.warnings`.
- UI can start with single seed selection but always send array (length 0 or 1). Future UI: multi-select / drag list of seed scenes.

## Extensibility & Future Work
- Cursor pagination + `nextCursor` token for incremental loading
- Score overlay badges (if `exposes_scores`)
- Hybrid provider (blends weighted outputs of child providers) – meta-provider pattern
- Remote provider adapter (HTTP) & caching layer
- Preference learning loop (feedback logging + model updates)
- Pruning context specialized providers (low engagement, low score, duplicates)
- Rich explanation objects per scene (`reasons`: [ {type, weight, label} ])

## Open Questions
- Global deduplication against already watched scenes – provider responsibility or global filter? (Likely provider opt-in flag.)
- Server persistence of user defaults – defer until demand.
- Security / sanitation for remote provider URLs & config injection.

## Implementation Phases
1. Registry + decorator + baseline providers (popularity, random, tag-overlap)
2. Endpoints + frontend dynamic dropdown + numeric/boolean/select field rendering
3. Hydration of scenes (remove per-id GraphQL fetch) + client pagination
4. Similar-scene context integration (single seed) then enable multi-seed UI
5. Advanced field types (tags/performers) + remote provider support
6. Score overlays, pruning context, cursor pagination

---
End of draft v2.
- tags / performers (future) -> multi-select async search

### Persistence Strategy
Use localStorage namespace: `ai.recs.{context}.{providerId}.{field}`.
At provider switch: hydrate defaults overridden by stored values.
Expose a "Reset Config" action (already have Reset button) to clear provider-specific overrides.

### Request Flow
When user clicks Refresh or provider/config changes:
1. Build payload with context/providerId/config + (seedSceneId if context similar_scene).
2. POST query endpoint.
3. Replace `scenes` state with returned list (no per-scene fetch).
4. Use existing pagination + zoom purely client-side over this list.

### Dynamic Form Rendering Sketch
```
fields.map(f => {
 switch (f.type) {
   case 'number': return <NumberInput ... />
   case 'slider': return <Slider min={f.min} max={f.max} step={f.step} />
   case 'select': return <Select options={f.options} />
   case 'boolean': return <Toggle />
 }
})
```

## Extensibility & Future Work
- Cursor pagination: provider returns `nextCursor`; UI appends additional scenes (infinite scroll option later).
- Score explanation overlays: if exposes_scores, show a toggle to display score badge on each card.
- Hybrid providers: chain results from multiple engines with blending weights (could itself be a provider with child provider selectors in config schema).
- Server-side user preference learning loop logging accepted / skipped scenes for training.
- Similar-scene inline widget: same provider registry with context=similar_scene; scene detail page requests with seedSceneId.
- Negative / prune recommendations: provider flag `is_pruning: true`; UI styles differently / asks confirmation before deletion actions.

## Open Questions
- Do we dedupe against already watched/played scenes automatically? (Probably provider-specific, but could add global filter flag.)
- Should provider list endpoint return persisted server-level defaults (per user)? (Phase 2.)
- Security: any provider config values need sanitation before being used in raw queries / embedding lookups.

## Implementation Phases
1. Registry + provider decorator + basic providers (popularity, random, similarity tag-overlap).
2. API endpoints + frontend dynamic dropdown + basic field rendering (number/select/boolean).
3. Hydration of scenes from provider (remove per-id GraphQL fetch in page) + client pagination.
4. Similar-scene context integration into scene detail.
5. Advanced field types + remote provider support.
6. Score overlays, pruning context, cursor pagination.

---
End of draft v1.
