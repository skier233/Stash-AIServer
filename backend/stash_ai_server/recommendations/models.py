from __future__ import annotations
from typing import List, Dict, Any, Callable, Awaitable, Optional
from enum import Enum
from pydantic import BaseModel, Field, validator

class RecContext(str, Enum):
    global_feed = "global_feed"
    similar_scene = "similar_scene"
    prune_candidates = "prune_candidates"

class RecommenderConfigField(BaseModel):
    name: str
    label: str
    # Added 'search' (styled search field variant of text)
    type: str = Field(..., description="number|slider|select|boolean|text|search|tags|performers|enum")
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: List[Dict[str, Any]] | None = None
    help: str | None = None
    # Per-field tag selector capabilities (frontend reads these to control UI)
    tag_combination: str | None = None
    constraint_types: List[str] | None = None
    allowed_combination_modes: List[str] | None = None

class RecommenderDefinition(BaseModel):
    id: str
    label: str
    description: str = ""
    contexts: List[RecContext]
    config: List[RecommenderConfigField] = []
    supports_limit_override: bool = True
    supports_pagination: bool = True
    needs_seed_scenes: bool = False
    allows_multi_seed: bool = True
    exposes_scores: bool = True

class SceneModel(BaseModel):
    """Hydrated scene contract returned to frontend.

    Extra fields are allowed so recommenders can attach experimental metadata
    (e.g., score, debug_meta, explanation). Core fields are validated so other
    developers immediately see shape errors instead of silently shipping an
    incompatible payload to the UI.
    """
    id: int
    title: Optional[str] = None
    rating100: Optional[int] = None
    studio: Optional[Dict[str, Any]] = None
    paths: Dict[str, Any] = Field(default_factory=dict)
    performers: List[Dict[str, Any]] = Field(default_factory=list)
    tags: List[Dict[str, Any]] = Field(default_factory=list)
    files: List[Dict[str, Any]] = Field(default_factory=list)
    score: Optional[float] = Field(None, description="Optional relevance score (0-1 or model-dependent)")
    debug_meta: Optional[Dict[str, Any]] = None

    @validator('paths', pre=True, always=True)
    def ensure_paths(cls, v):  # type: ignore
        if not isinstance(v, dict):
            return {'screenshot': None, 'preview': None}
        v.setdefault('screenshot', None)
        v.setdefault('preview', None)
        return v

    class Config:
        extra = 'allow'

RecommendationResult = List[Dict[str, Any]]

class RecommendationRequest(BaseModel):
    context: RecContext
    recommenderId: str
    config: Dict[str, Any] = {}
    seedSceneIds: List[int] | None = None
    limit: int | None = None
    offset: int | None = 0

RecommenderHandler = Callable[[Dict[str, Any], RecommendationRequest], RecommendationResult | Awaitable[RecommendationResult]]
