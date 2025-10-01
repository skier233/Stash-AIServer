from fastapi import APIRouter
from sqlalchemy import text
from app.core.config import settings
from app.db.session import SessionLocal

router = APIRouter()


@router.get('/version')
async def version():
    db_version = None
    try:
        with SessionLocal() as db:
            try:
                res = db.execute(text('SELECT version_num FROM alembic_version'))
                row = res.first()
                if row:
                    db_version = row[0]
            except Exception:
                db_version = None
    except Exception:
        db_version = None
    return {
        'version': settings.version,
        'db_alembic_head': db_version,
    }
