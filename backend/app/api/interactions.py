from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import List, Union
from app.db.session import get_db
from app.schemas.interaction import InteractionEventIn, InteractionIngestResult
from app.services.interactions import ingest_events
from sqlalchemy import select, func
from app.models.interaction import InteractionEvent, InteractionSession, SceneWatchSegment, SceneDerived

router = APIRouter(prefix='/interactions', tags=['interactions'])


@router.post('/sync', response_model=InteractionIngestResult)
async def sync_events(body: Union[List[InteractionEventIn], InteractionEventIn], request: Request, db: Session = Depends(get_db)):
    """Ingest interaction events.

    Accepts either a JSON array of events (preferred) or a single event object
    for backward/edge-case compatibility. If a single object is sent, we wrap
    it into a list before processing.
    """
    if isinstance(body, list):
        events = body
        shape = 'list'
    else:
        events = [body]
        shape = 'single'
    accepted, duplicates, errors = ingest_events(db, events)
    if shape == 'single':
        try:
            print(f'[ingest_warn] single object received at /sync; consider sending an array. session_ids={[e.session_id for e in events]}', flush=True)
        except Exception:
            pass
    if errors:
        try:
            print(f'[ingest_errors] accepted={accepted} duplicates={duplicates} errors={errors} shape={shape}', flush=True)
        except Exception:
            pass
    return InteractionIngestResult(accepted=accepted, duplicates=duplicates, errors=errors)
