from typing import Any, Dict

from fastapi import APIRouter
from sqlalchemy import text

from stash_ai_server.core.config import settings
from stash_ai_server.core.compat import FRONTEND_MIN_VERSION
from stash_ai_server.db.session import SessionLocal

router = APIRouter()


def get_version_payload() -> Dict[str, Any]:
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
        'frontend_min_version': FRONTEND_MIN_VERSION or None,
    }


@router.get('/version')
async def version():
    return get_version_payload()
