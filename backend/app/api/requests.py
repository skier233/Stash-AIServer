from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.ai_request import AIRequest
from app.schemas.ai_request import AIRequestCreate, AIRequestRead

router = APIRouter(prefix='/requests', tags=['requests'])

@router.post('/', response_model=AIRequestRead, status_code=201)
def create_request(payload: AIRequestCreate, db: Session = Depends(get_db)):
    obj = AIRequest(prompt=payload.prompt)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

@router.get('/', response_model=list[AIRequestRead])
def list_requests(db: Session = Depends(get_db)):
    return db.query(AIRequest).order_by(AIRequest.id.desc()).limit(100).all()

@router.get('/{request_id}', response_model=AIRequestRead)
def get_request(request_id: int, db: Session = Depends(get_db)):
    obj = db.query(AIRequest).filter(AIRequest.id == request_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail='Request not found')
    return obj
