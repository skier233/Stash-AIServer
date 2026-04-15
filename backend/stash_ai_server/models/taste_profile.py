"""ORM models for precomputed user taste profiles.

Caches the system's understanding of user preferences so the recommender
can serve results instantly without recomputing tag TF-IDF, performer
affinity, and embedding centroids on every request.

Also stores content clusters — groups of similar scenes bucketed
by embedding proximity and/or tag similarity — so the recommender
can operate at the cluster level for coarse recommendations.
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from stash_ai_server.db.session import Base


class UserTasteProfile(Base):
    """Snapshot of a computed user taste profile.

    Stores the top tag / performer affinities, engagement stats, and
    metadata about how/when the profile was built.  One row per
    ``profile_type`` (e.g. "global", "recent_30d", etc.).
    """

    __tablename__ = "user_taste_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_type: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, unique=True,
    )  # e.g. "global", "recent_30d"
    watched_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    liked_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    disliked_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # Tag affinities: [{tag_id, tag_name, affinity, positive_weight, negative_weight, doc_freq}, ...]
    tag_affinities = mapped_column(sa.JSON, nullable=False, server_default="[]")
    # Negative tags (not in tag_affinities)
    negative_tags = mapped_column(sa.JSON, nullable=False, server_default="[]")
    # Performer affinities
    performer_affinities = mapped_column(sa.JSON, nullable=False, server_default="[]")
    negative_performers = mapped_column(sa.JSON, nullable=False, server_default="[]")
    # Engagement stats
    engagement_stats = mapped_column(sa.JSON, nullable=False, server_default="{}")
    # Embedding coverage
    embedding_coverage_pct: Mapped[float] = mapped_column(sa.REAL, nullable=True)
    # Build metadata
    computed_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    computation_ms: Mapped[int] = mapped_column(sa.Integer, nullable=True)
    config_json = mapped_column(sa.JSON, nullable=True)  # params used to build


class TasteCentroid(Base):
    """Embedding-space centroid representing user taste direction.

    Stores the average embedding vector of liked (or disliked) content
    per embedding type, so the recommender can do a single cosine distance
    against new candidates instead of comparing against every watched scene.
    """

    __tablename__ = "taste_centroids"

    id: Mapped[int] = mapped_column(primary_key=True)
    centroid_type: Mapped[str] = mapped_column(
        sa.String(40), nullable=False,
    )  # "liked" or "disliked"
    embedding_type: Mapped[str] = mapped_column(
        sa.String(50), nullable=False,
    )  # e.g. "visual_metaclip2", "audio_speech"
    centroid = mapped_column(Vector(), nullable=False)
    dim: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    scene_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    computed_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint("centroid_type", "embedding_type", name="uq_taste_centroid_type_embed"),
    )


class ContentCluster(Base):
    """A bucket of similar scenes grouped by tag/embedding proximity.

    Clusters are recomputed periodically.  Each cluster has a label, a
    centroid, representative scenes, and aggregate stats.  The recommender
    can use clusters to diversify results or drill into niches.
    """

    __tablename__ = "content_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_label: Mapped[str] = mapped_column(sa.String(200), nullable=True)
    # Top tags describing this cluster: [{tag_id, tag_name, weight}, ...]
    top_tags = mapped_column(sa.JSON, nullable=False, server_default="[]")
    centroid = mapped_column(Vector(), nullable=True)
    dim: Mapped[int] = mapped_column(sa.Integer, nullable=True)
    scene_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # Stats
    avg_engagement: Mapped[float] = mapped_column(sa.REAL, nullable=True)
    user_affinity: Mapped[float] = mapped_column(sa.REAL, nullable=True)  # how much user likes this cluster
    computed_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class ContentClusterMember(Base):
    """Maps scenes to their assigned cluster."""

    __tablename__ = "content_cluster_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        sa.ForeignKey("content_clusters.id", ondelete="CASCADE"), nullable=False,
    )
    scene_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    distance: Mapped[float] = mapped_column(sa.REAL, nullable=True)  # distance from centroid

    __table_args__ = (
        sa.UniqueConstraint("cluster_id", "scene_id", name="uq_cluster_member"),
        sa.Index("ix_cluster_member_scene", "scene_id"),
    )
