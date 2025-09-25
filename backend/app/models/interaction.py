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
    # Only store client timestamp; remove server-received ts and other heavy fields
    client_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # event metadata (positions etc.)
    event_metadata: Mapped[dict | None] = mapped_column('metadata', JSON, nullable=True)

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
    # store last client event ts for quick lookup
    last_event_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # store session start (first event) timestamp
    session_start_ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_scene_event_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    # optional client fingerprint and ip for session merging across tabs/refresh
    client_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

# Aggregated per-session per-scene summary (built on ingest for now)
class SceneWatchSegment(Base):
    __tablename__ = 'scene_watch_segments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    scene_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    watched_s: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SceneDerived(Base):
    __tablename__ = 'scene_derived'
    scene_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    derived_o_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # total number of explicit views (increments per scene_view event)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ImageDerived(Base):
    __tablename__ = 'image_derived'
    image_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    derived_o_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
