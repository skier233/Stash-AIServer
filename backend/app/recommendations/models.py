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
    type: str = Field(..., description="number|slider|select|boolean|text|tags|performers|enum")
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: List[Dict[str, Any]] | None = None
    help: str | None = None

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

    This is intentionally minimal; additional fields can be appended later
    without breaking existing consumers as long as they remain optional on
    the frontend. Keeping a Pydantic model here ensures uniform shape across
    all recommenders and centralizes any normalization logic we later add.
    """
    id: int
    title: Optional[str] = None
    rating100: Optional[int] = None
    studio: Optional[Dict[str, Any]] = None
    paths: Dict[str, Any] = Field(default_factory=dict)
    performers: List[Dict[str, Any]] = Field(default_factory=list)
    tags: List[Dict[str, Any]] = Field(default_factory=list)
    files: List[Dict[str, Any]] = Field(default_factory=list)

    @validator('paths', pre=True, always=True)
    def ensure_paths(cls, v):  # type: ignore
        if not isinstance(v, dict):
            return {'screenshot': None, 'preview': None}
        v.setdefault('screenshot', None)
        v.setdefault('preview', None)
        return v

RecommendationResult = List[Dict[str, Any]]

class RecommendationRequest(BaseModel):
    context: RecContext
    recommenderId: str
    config: Dict[str, Any] = {}
    seedSceneIds: List[int] | None = None
    limit: int | None = None

RecommenderHandler = Callable[[Dict[str, Any], RecommendationRequest], RecommendationResult | Awaitable[RecommendationResult]]
