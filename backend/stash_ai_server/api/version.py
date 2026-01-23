from typing import Any, Dict

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import Session

from stash_ai_server.core.config import settings
from stash_ai_server.core.compat import FRONTEND_MIN_VERSION

router = APIRouter()


def get_version_payload(db: Session = None) -> Dict[str, Any]:
    db_version = None
    if db is not None:
        # Use provided session (for testing)
        try:
            res = db.execute(text('SELECT version_num FROM alembic_version'))
            row = res.first()
            if row:
                db_version = row[0]
        except Exception:
            db_version = None
    else:
        # Try to get database version, but don't hang if database is unavailable
        try:
            from stash_ai_server.db.session import create_engine
            from sqlalchemy import create_engine as sa_create_engine
            
            # Create engine with short timeout to prevent hanging
            engine = sa_create_engine(
                settings.database_url,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 2}  # 2 second timeout
            )
            
            with engine.connect() as conn:
                res = conn.execute(text('SELECT version_num FROM alembic_version'))
                row = res.first()
                if row:
                    db_version = row[0]
            
            engine.dispose()
                    
        except Exception:
            # Database unavailable or timeout - that's OK for version endpoint
            db_version = None
    
    return {
        'version': settings.version,
        'db_alembic_head': db_version,
        'frontend_min_version': FRONTEND_MIN_VERSION or None,
    }


@router.get('/version')
async def version():
    """Get version information. Works even when database is unavailable."""
    return get_version_payload()
