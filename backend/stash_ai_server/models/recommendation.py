from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from stash_ai_server.db.session import Base

class RecommendationPreference(Base):
    __tablename__ = 'recommendation_preferences'
    __table_args__ = (
        UniqueConstraint('context', name='uq_recommendation_preferences_context'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    context: Mapped[str] = mapped_column(String(64), nullable=False)
    recommender_id: Mapped[str] = mapped_column(String(100), nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
        nullable=False,
    )
