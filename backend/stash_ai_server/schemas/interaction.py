from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any, List, Optional

PRIMITIVE_EVENT_TYPES = {
    'session_start','session_end','scene_view','scene_page_enter','scene_page_leave',
    'scene_watch_start','scene_watch_pause','scene_seek','scene_watch_progress',
    'scene_watch_complete','image_view','gallery_view'
}

class InteractionEventIn(BaseModel):
    id: str = Field(alias='id')  # client side event id
    session_id: str
    client_id: str | None = None
    ts: datetime  # client timestamp (original event ts)
    type: str
    entity_type: str
    entity_id: int
    metadata: Optional[dict[str, Any]] = None
    # keep metadata only; page_url/user_agent/viewport/schema_version removed

    class Config:
        allow_population_by_field_name = True

class InteractionIngestResult(BaseModel):
    accepted: int
    duplicates: int
    errors: List[str] = []

# legacy SceneWatchSummaryRead removed — summaries are no longer produced by the backend
