"""ORM model for entity-level content embeddings.

Stores dense vector embeddings produced by audio (ECAPA-TDNN) and visual
(DINOv3, MetaCLIP2) models.  Uses pgvector for native vector operations.
Designed for the recommendation engine to query similar entities by cosine
similarity across multiple embedding types.

Entities can be scenes (entity_type="scene") or images (entity_type="image").
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from stash_ai_server.db.session import Base


class EntityEmbedding(Base):
    """A single content embedding for a scene or image.

    Multiple rows per entity are expected — one per ``embedding_type``
    (e.g. ``audio_speech``, ``audio_moan``,
    ``visual_dinov3_section_0``, ``visual_metaclip2_section_0``).

    The ``embedding`` column uses pgvector without a fixed dimension so
    that different model families (192-dim ECAPA, 768-dim DINOv3, etc.)
    can coexist.  HNSW indexes will be added per-type via partial indexes
    once dimensions are finalized.
    """

    __tablename__ = "entity_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("ai_model_runs.id", ondelete="SET NULL"), nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    embedding_type: Mapped[str] = mapped_column(
        sa.String(50), nullable=False,
    )  # e.g. "audio_speech", "audio_moan", "visual_dinov3_section_0"
    embedding = mapped_column(Vector(), nullable=False)
    dim: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    embedder: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    norm: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    sample_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default="1",
    )
    start_time: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)
    end_time: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", sa.JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.Index("ix_entity_emb_entity", "entity_type", "entity_id"),
        sa.Index("ix_entity_emb_type", "embedding_type"),
        sa.UniqueConstraint(
            "entity_type", "entity_id", "embedding_type", "embedder",
            name="uq_entity_embedding_entity_type_embedder",
        ),
        sa.Index("ix_entity_emb_run", "run_id"),
    )


__all__ = ["EntityEmbedding"]
