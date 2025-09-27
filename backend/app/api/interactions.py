from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import List, Union
from app.db.session import get_db
from app.schemas.interaction import InteractionEventIn, InteractionIngestResult
from app.services.interactions import ingest_events
import hashlib
from sqlalchemy import select, func
from app.models.interaction import InteractionEvent, InteractionSession, SceneWatchSegment, SceneDerived

router = APIRouter(prefix='/interactions', tags=['interactions'])


@router.post('/sync', response_model=InteractionIngestResult)
async def sync_events(body: List[InteractionEventIn], request: Request, db: Session = Depends(get_db)):
    """Ingest interaction events.

    Accepts either a JSON array of events (preferred) or a single event object
    for backward/edge-case compatibility. If a single object is sent, we wrap
    it into a list before processing.
    """
    events = body
    # If the client included a persistent client_id in the event payload, prefer that
    # as the canonical client fingerprint. Otherwise fall back to hashing IP+UA.
    # prefer client_id if present on the first event
    provided_client_id = None
    if events and len(events) > 0:
        provided_client_id = getattr(events[0], 'client_id', None)

    if provided_client_id:
        client_fingerprint = str(provided_client_id)
    else:
        # Fallback to IP+UA hash for fingerprinting
        try:
            client_ip = request.client.host
        except Exception:
            client_ip = None
        ua = request.headers.get('user-agent', '')
        fp_src = (str(client_ip or '') + '|' + ua)[:256]
        client_fingerprint = hashlib.sha256(fp_src.encode('utf-8')).hexdigest()

    accepted, duplicates, errors = ingest_events(db, events, client_fingerprint=client_fingerprint)
    if errors:
        try:
            print(f'[ingest_errors] accepted={accepted} duplicates={duplicates} errors={errors}', flush=True)
        except Exception:
            pass
    return InteractionIngestResult(accepted=accepted, duplicates=duplicates, errors=errors)
