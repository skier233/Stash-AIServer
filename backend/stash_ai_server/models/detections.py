"""ORM models for detection tracks, face clusters, and face embeddings.

These are core server tables (not plugin-specific) so that any plugin
can store and query visual-recognition data using these shared models.
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stash_ai_server.db.session import Base


class DetectionTrack(Base):
    """A single detected object (or temporal track for video).

    For images: one row per detection (no temporal span).
    For video: one row per track, compressed from many per-frame detections
    into a single ``start_s`` / ``end_s`` span with a representative bbox.
    """

    __tablename__ = "detection_tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        sa.ForeignKey("ai_model_runs.id", ondelete="CASCADE"), nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    label: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    bbox: Mapped[list] = mapped_column(sa.ARRAY(sa.REAL, dimensions=1), nullable=False)
    score: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    detector: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    start_s: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)
    end_s: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)
    keyframes: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", sa.JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    # Relationships
    embeddings: Mapped[list["FaceEmbedding"]] = relationship(
        "FaceEmbedding", back_populates="track", cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.Index("ix_det_tracks_run", "run_id"),
        sa.Index("ix_det_tracks_entity", "entity_type", "entity_id"),
        sa.Index("ix_det_tracks_label", "label"),
    )


class FaceCluster(Base):
    """Identity group for face recognition.

    One row per recognised "person".  Before performer linking the status
    is ``unidentified``; after linking it becomes ``identified`` and
    ``performer_id`` is set.
    """

    __tablename__ = "face_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default="unidentified",
    )
    performer_id: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    centroid = mapped_column(Vector(512), nullable=True)
    sample_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default="0",
    )
    quality_score: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)
    merged_into_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("face_clusters.id", ondelete="SET NULL"), nullable=True,
    )
    label: Mapped[str | None] = mapped_column(sa.String(150), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    # Relationships
    embeddings: Mapped[list["FaceEmbedding"]] = relationship(
        "FaceEmbedding",
        back_populates="cluster",
        foreign_keys="FaceEmbedding.cluster_id",
    )

    __table_args__ = (
        sa.Index("ix_face_clusters_status", "status"),
        sa.Index("ix_face_clusters_performer", "performer_id"),
        # HNSW index created via raw SQL in the migration
    )


class FaceEmbedding(Base):
    """A single face embedding vector, linked to a detection track and a cluster."""

    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(
        sa.ForeignKey("detection_tracks.id", ondelete="CASCADE"), nullable=False,
    )
    cluster_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("face_clusters.id", ondelete="SET NULL"), nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    embedding = mapped_column(Vector(512), nullable=False)
    norm: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    score: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    bbox: Mapped[list | None] = mapped_column(
        sa.ARRAY(sa.REAL, dimensions=1), nullable=True,
    )
    timestamp_s: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)
    is_exemplar: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default="false",
    )
    embedder: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    # Relationships
    track: Mapped[DetectionTrack] = relationship(
        "DetectionTrack", back_populates="embeddings",
    )
    cluster: Mapped[FaceCluster | None] = relationship(
        "FaceCluster", back_populates="embeddings",
    )

    __table_args__ = (
        sa.Index("ix_face_emb_track", "track_id"),
        sa.Index("ix_face_emb_cluster", "cluster_id"),
        sa.Index("ix_face_emb_entity", "entity_type", "entity_id"),
        # Partial index and HNSW index created via raw SQL in the migration
    )


__all__ = [
    "DetectionTrack",
    "FaceCluster",
    "FaceEmbedding",
]
