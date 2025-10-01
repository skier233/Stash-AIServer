from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import List, Union
from stash_ai_server.db.session import get_db
from stash_ai_server.schemas.interaction import InteractionEventIn, InteractionIngestResult
from stash_ai_server.services.interactions import ingest_events
import hashlib
from sqlalchemy import select, func
from stash_ai_server.models.interaction import InteractionEvent, InteractionSession, SceneWatchSegment, SceneDerived

router = APIRouter(prefix='/interactions', tags=['interactions'])


@router.post('/sync', response_model=InteractionIngestResult)
async def sync_events(body: List[InteractionEventIn], request: Request, db: Session = Depends(get_db)):
    """Ingest interaction events and return an ingest summary."""
    events = body
    # Prefer explicit client_id, else hash IP+UA to form a fingerprint
    provided_client_id = None
    if events:
        provided_client_id = getattr(events[0], 'client_id', None)

    if provided_client_id:
        client_fingerprint = str(provided_client_id)
    else:
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
