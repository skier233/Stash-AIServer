from __future__ import annotations
from sqlalchemy import Integer, String, DateTime, JSON, Float, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.db.session import Base

# Raw interaction events (append-only, with client-side id for dedupe)
class InteractionEvent(Base):
    __tablename__ = 'interaction_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True, default=datetime.utcnow, nullable=False)  # server receipt time
    client_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 'metadata' is reserved by SQLAlchemy declarative; use attribute name event_metadata while keeping DB column name 'metadata'
    event_metadata: Mapped[dict | None] = mapped_column('metadata', JSON, nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    viewport_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    viewport_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint('client_event_id', name='uq_interaction_client_event_id'),
        Index('ix_interaction_session_scene', 'session_id', 'entity_type', 'entity_id'),
        Index('ix_interaction_client_ts', 'client_ts'),
    )

# Session level quick lookup (last scene)
class InteractionSession(Base):
    __tablename__ = 'interaction_sessions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_event_ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_scene_event_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

# Aggregated per-session per-scene summary (built on ingest for now)
class SceneWatchSummary(Base):
    __tablename__ = 'scene_watch_summaries'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    scene_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    total_watched_s: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    percent_watched: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)  # store as 0/1
    segments: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of {start,end}
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('session_id', 'scene_id', name='uq_scene_watch_session_scene'),
    )
