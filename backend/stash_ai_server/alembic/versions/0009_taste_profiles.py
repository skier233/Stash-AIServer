"""User taste profiles, centroids, and content clusters

Precomputed tables for the recommendation engine:
  - user_taste_profiles  — cached tag/performer affinities + engagement stats
  - taste_centroids      — embedding-space centroids (liked/disliked)
  - content_clusters     — buckets of similar scenes
  - content_cluster_members — scene → cluster mapping

Revision ID: 0009_taste_profiles
Revises: 0008_scene_embeddings
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_taste_profiles"
down_revision = "0008_scene_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- user_taste_profiles ---
    op.create_table(
        "user_taste_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("profile_type", sa.String(40), nullable=False, unique=True),
        sa.Column("watched_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("liked_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("disliked_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tag_affinities", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("negative_tags", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("performer_affinities", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("negative_performers", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("engagement_stats", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("embedding_coverage_pct", sa.REAL, nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("computation_ms", sa.Integer, nullable=True),
        sa.Column("config_json", sa.JSON, nullable=True),
    )

    # --- taste_centroids (pgvector) ---
    op.execute("""
        CREATE TABLE taste_centroids (
            id SERIAL PRIMARY KEY,
            centroid_type VARCHAR(40) NOT NULL,
            embedding_type VARCHAR(50) NOT NULL,
            centroid vector NOT NULL,
            dim INTEGER NOT NULL,
            scene_count INTEGER NOT NULL DEFAULT 0,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_taste_centroid_type_embed UNIQUE (centroid_type, embedding_type)
        )
    """)

    # --- content_clusters (pgvector centroid) ---
    op.execute("""
        CREATE TABLE content_clusters (
            id SERIAL PRIMARY KEY,
            cluster_label VARCHAR(200),
            top_tags JSONB NOT NULL DEFAULT '[]',
            centroid vector,
            dim INTEGER,
            scene_count INTEGER NOT NULL DEFAULT 0,
            avg_engagement REAL,
            user_affinity REAL,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- content_cluster_members ---
    op.create_table(
        "content_cluster_members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cluster_id", sa.Integer, sa.ForeignKey("content_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", sa.Integer, nullable=False),
        sa.Column("distance", sa.REAL, nullable=True),
        sa.UniqueConstraint("cluster_id", "scene_id", name="uq_cluster_member"),
        sa.Index("ix_cluster_member_scene", "scene_id"),
    )


def downgrade() -> None:
    op.drop_table("content_cluster_members")
    op.execute("DROP TABLE IF EXISTS content_clusters")
    op.execute("DROP TABLE IF EXISTS taste_centroids")
    op.drop_table("user_taste_profiles")
