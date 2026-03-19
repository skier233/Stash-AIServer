"""Add detection_tracks, face_clusters, and face_embeddings tables

Revision ID: 0005_detection_face_tables
Revises: 0004_ai_tagging_perf_indexes
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_detection_face_tables'
down_revision = '0004_ai_tagging_perf_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── detection_tracks ──────────────────────────────────────────────
    op.create_table(
        "detection_tracks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("ai_model_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("bbox", sa.ARRAY(sa.REAL, dimensions=1), nullable=False),
        sa.Column("score", sa.REAL, nullable=False),
        sa.Column("detector", sa.String(100), nullable=False),
        sa.Column("start_s", sa.REAL, nullable=True),
        sa.Column("end_s", sa.REAL, nullable=True),
        sa.Column("keyframes", sa.JSON, nullable=True),
        sa.Column("metadata", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_det_tracks_run", "detection_tracks", ["run_id"])
    op.create_index("ix_det_tracks_entity", "detection_tracks", ["entity_type", "entity_id"])
    op.create_index("ix_det_tracks_label", "detection_tracks", ["label"])

    # ── face_clusters ─────────────────────────────────────────────────
    op.create_table(
        "face_clusters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="unidentified"),
        sa.Column("performer_id", sa.Integer, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quality_score", sa.REAL, nullable=True),
        sa.Column("merged_into_id", sa.Integer, sa.ForeignKey("face_clusters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_face_clusters_status", "face_clusters", ["status"])
    op.create_index("ix_face_clusters_performer", "face_clusters", ["performer_id"])

    # Add centroid vector column and HNSW index via raw SQL (pgvector types)
    op.execute("ALTER TABLE face_clusters ADD COLUMN centroid vector(512)")
    op.execute(
        "CREATE INDEX ix_face_clusters_centroid ON face_clusters "
        "USING hnsw (centroid vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # ── face_embeddings ───────────────────────────────────────────────
    op.create_table(
        "face_embeddings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("track_id", sa.Integer, sa.ForeignKey("detection_tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", sa.Integer, sa.ForeignKey("face_clusters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("norm", sa.REAL, nullable=False),
        sa.Column("score", sa.REAL, nullable=False),
        sa.Column("bbox", sa.ARRAY(sa.REAL, dimensions=1), nullable=True),
        sa.Column("timestamp_s", sa.REAL, nullable=True),
        sa.Column("is_exemplar", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("embedder", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_face_emb_track", "face_embeddings", ["track_id"])
    op.create_index("ix_face_emb_cluster", "face_embeddings", ["cluster_id"])
    op.create_index("ix_face_emb_entity", "face_embeddings", ["entity_type", "entity_id"])

    # Partial index on exemplars only
    op.execute(
        "CREATE INDEX ix_face_emb_exemplar ON face_embeddings (cluster_id, is_exemplar) "
        "WHERE is_exemplar = true"
    )

    # Embedding vector column and HNSW index via raw SQL
    op.execute("ALTER TABLE face_embeddings ADD COLUMN embedding vector(512) NOT NULL")
    op.execute(
        "CREATE INDEX ix_face_emb_vector ON face_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("face_embeddings")
    op.drop_table("face_clusters")
    op.drop_table("detection_tracks")
