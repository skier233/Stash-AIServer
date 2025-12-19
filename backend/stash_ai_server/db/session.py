from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from stash_ai_server.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=int(settings.db_credentials['pool_size']),
    max_overflow=int(settings.db_credentials['max_overflow']),
    echo=bool(settings.db_credentials['echo']),
    future=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
