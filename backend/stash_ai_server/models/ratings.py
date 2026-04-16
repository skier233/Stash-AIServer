"""ORM model for the extensible entity ratings system.

Stores numeric ratings (0-100) for any entity type with an optional
rating key to support multiple rating dimensions per entity.
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from stash_ai_server.db.session import Base


class EntityRating(Base):
    """A single rating value for an entity.

    Supports arbitrary entity types (``face_cluster``, ``scene``, etc.)
    and multiple rating dimensions via ``rating_key`` (defaults to
    ``"default"``).  Values are stored as 0-100 integers, matching
    Stash's ``rating100`` convention.
    """

    __tablename__ = "entity_ratings"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(
        sa.String(50), nullable=False,
    )
    entity_id: Mapped[str] = mapped_column(
        sa.String(200), nullable=False,
    )
    rating_key: Mapped[str] = mapped_column(
        sa.String(100), nullable=False, server_default="default",
    )
    value: Mapped[int] = mapped_column(
        sa.Integer, nullable=False,
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "entity_type", "entity_id", "rating_key",
            name="uq_entity_rating",
        ),
        sa.Index("ix_entity_ratings_entity", "entity_type", "entity_id"),
    )
