from __future__ import annotations
from time import timezone
from sqlalchemy import Integer, String, DateTime, JSON, Float, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
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
    # last_scene_id and last_scene_event_ts removed; use generic last_entity_* fields
    # Generic last-entity tracking so we can compute "last item viewed in session"
    last_entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_entity_event_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    # client fingerprint for session merging across tabs/refresh
    client_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


# Optional alias mapping from an incoming frontend session id to a canonical backend session id.
# This ensures that when we opportunistically merge a new incoming session id into an
# existing canonical session (soft continuation), subsequent events using the same
# incoming id remain consistently mapped during the alias TTL.
class InteractionSessionAlias(Base):
    __tablename__ = 'interaction_session_aliases'
    alias_session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), nullable=False)

# Aggregated per-session per-scene summary (built on ingest for now)
class SceneWatch(Base):
    __tablename__ = 'scene_watch'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    scene_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # page visit timing
    page_entered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    page_left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # watch statistics for this page visit
    total_watched_s: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    watch_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), nullable=False)


class SceneWatchSegment(Base):
    __tablename__ = 'scene_watch_segments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scene_watch_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # FK to scene_watch
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


# Persisted library search/filter events for quick analytics
class InteractionLibrarySearch(Base):
    __tablename__ = 'interaction_library_search'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # either 'scenes' or 'images' (frontend should set entity_type to 'library' and entity_id to 'scenes'/'images')
    library: Mapped[str] = mapped_column(String(20), nullable=False)
    # raw search string
    query: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # structured filters JSON (tags, performers, etc.)
    filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
