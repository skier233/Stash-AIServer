from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.schemas.interaction import InteractionEventIn, InteractionIngestResult, SceneWatchSummaryRead
from app.services.interactions import ingest_events
from app.models.interaction import SceneWatchSummary
from sqlalchemy import select

router = APIRouter(prefix='/interactions', tags=['interactions'])

@router.post('/sync', response_model=InteractionIngestResult)
async def sync_events(events: List[InteractionEventIn], db: Session = Depends(get_db)):
    accepted, duplicates, errors = ingest_events(db, events)
    return InteractionIngestResult(accepted=accepted, duplicates=duplicates, errors=errors)

@router.post('/track', response_model=InteractionIngestResult)
async def track_event(event: InteractionEventIn, db: Session = Depends(get_db)):
    # Reuse batch ingest logic with a single-item list
    accepted, duplicates, errors = ingest_events(db, [event])
    return InteractionIngestResult(accepted=accepted, duplicates=duplicates, errors=errors)

@router.get('/session/{session_id}/scene/{scene_id}/summary', response_model=SceneWatchSummaryRead | None)
async def get_scene_summary(session_id: str, scene_id: str, db: Session = Depends(get_db)):
    row = db.execute(select(SceneWatchSummary).where(SceneWatchSummary.session_id==session_id, SceneWatchSummary.scene_id==scene_id)).scalar_one_or_none()
    return row
