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
    cluster_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("face_clusters.id", ondelete="SET NULL"), nullable=True,
    )
    keyframes: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    # Reserved for plugin-specific track metadata (e.g. source info).
    # Written by detection_store.store_detection_track(); may be None.
    metadata_: Mapped[dict | None] = mapped_column("metadata", sa.JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    # Relationships
    embeddings: Mapped[list["FaceEmbedding"]] = relationship(
        "FaceEmbedding", back_populates="track", cascade="all, delete-orphan",
    )
    cluster: Mapped["FaceCluster | None"] = relationship(
        "FaceCluster",
        foreign_keys=[cluster_id],
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
    # Audit trail: when cluster B is merged into cluster A, B's
    # ``merged_into_id`` is set to A and B's status becomes
    # ``merged_away``.  This allows tracing merge history but is
    # NOT used for runtime lookups (status filtering is sufficient).
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
    stashdb_match: Mapped["StashDBPerformerRef | None"] = relationship(
        "StashDBPerformerRef",
        foreign_keys="FaceCluster.stashdb_match_id",
        uselist=False,
    )

    # StashDB suggestion columns (set during clustering, before user confirms)
    stashdb_match_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("stashdb_performer_refs.id", ondelete="SET NULL"), nullable=True,
    )
    stashdb_match_score: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)

    # Rejection state: user explicitly marked suggestions as wrong
    stashdb_suggestion_rejected: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.false(),
    )
    # JSON list of local performer IDs rejected as co-occurrence suggestions
    rejected_performer_ids: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    __table_args__ = (
        sa.Index("ix_face_clusters_status", "status"),
        sa.Index("ix_face_clusters_performer", "performer_id"),
        # ix_face_clusters_stashdb (partial), ix_face_clusters_unmatched (partial),
        # and ix_face_clusters_centroid (HNSW) are created via raw SQL in the migration.
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
        sa.Index("ix_face_emb_cluster_entity", "cluster_id", "entity_type", "entity_id"),
        # ix_face_emb_exemplar (partial) and ix_face_emb_vector (HNSW)
        # are created via raw SQL in the migration.
    )


class StashDBPerformerRef(Base):
    """Imported reference centroid for a StashDB performer.

    These rows are created by importing a ``.saie`` pack.  They store a
    single centroid vector per performer so that newly-detected faces can
    be matched against StashDB performers before a local Stash performer
    exists.  When the user confirms a match, ``local_performer_id`` is
    set and a real Stash performer is created on demand.
    """

    __tablename__ = "stashdb_performer_refs"

    id: Mapped[int] = mapped_column(primary_key=True)
    stashdb_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    disambiguation: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    aliases: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    centroid = mapped_column(Vector(512), nullable=False)
    sample_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    quality_score: Mapped[float | None] = mapped_column(sa.REAL, nullable=True)
    embedder: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    source_endpoint: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    pack_id: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    local_performer_id: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    image_url: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    # Extra endpoints this performer appears in (e.g. TPDB + StashDB)
    extra_endpoints: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.Index("ix_stashdb_ref_stashdb_id", "stashdb_id"),
        sa.Index("ix_stashdb_ref_pack", "pack_id"),
        sa.Index("ix_stashdb_ref_name", "name"),
        # ix_stashdb_ref_local_perf (partial) and ix_stashdb_ref_endpoint (partial)
        # are created via raw SQL in the migration.
    )


class FacePerformerAssignment(Base):
    """Tracks performer-to-entity assignments made by the face recognition system.

    One row per (entity_type, entity_id, performer_id, cluster_id) tuple.
    Only created when the performer was NOT already present on the entity
    at assignment time, so we know the AI system was the origin.
    Used to safely decide whether to remove a performer when face links
    are pruned — we never remove a performer we didn't originally add.
    """

    __tablename__ = "face_performer_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    performer_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    cluster_id: Mapped[int] = mapped_column(
        sa.ForeignKey("face_clusters.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "entity_type", "entity_id", "performer_id", "cluster_id",
            name="uq_face_performer_assignment",
        ),
        sa.Index("ix_fpa_entity_performer", "entity_type", "entity_id", "performer_id"),
        sa.Index("ix_fpa_cluster", "cluster_id"),
    )


__all__ = [
    "DetectionTrack",
    "FaceCluster",
    "FaceEmbedding",
    "FacePerformerAssignment",
    "StashDBPerformerRef",
]
