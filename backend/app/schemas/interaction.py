from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any, List, Optional

PRIMITIVE_EVENT_TYPES = {
    'session_start','session_end','scene_view','scene_watch_start','scene_watch_pause',
    'scene_seek','scene_watch_progress','scene_watch_complete','image_view','gallery_view'
}

class InteractionEventIn(BaseModel):
    id: str = Field(alias='id')  # client side event id
    session_id: str
    ts: datetime  # client timestamp (original event ts)
    type: str
    entity_type: str
    entity_id: str
    metadata: Optional[dict[str, Any]] = None
    page_url: Optional[str] = None
    user_agent: Optional[str] = None
    viewport: Optional[dict[str,int]] = None
    schema_version: int

    class Config:
        allow_population_by_field_name = True

class InteractionIngestResult(BaseModel):
    accepted: int
    duplicates: int
    errors: List[str] = []

class SceneWatchSummaryRead(BaseModel):
    session_id: str
    scene_id: str
    total_watched_s: float
    duration_s: float | None = None
    percent_watched: float | None = None
    completed: bool
    segments: list[dict[str, float]] | None = None

    class Config:
        from_attributes = True
