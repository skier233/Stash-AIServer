"""Add face recognition tables: detection_tracks, face_clusters, face_embeddings,
stashdb_performer_refs, and face_performer_assignments.

Consolidated from migrations 0005–0010 (no users of face-rec tables yet).

Revision ID: 0005_face_recognition
Revises: 0004_ai_tagging_perf_indexes
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_face_recognition'
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
    # cluster_id is added after face_clusters table is created (see below)

    # ── stashdb_performer_refs ────────────────────────────────────────
    # Created before face_clusters because face_clusters has an FK to it.
    op.create_table(
        "stashdb_performer_refs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("stashdb_id", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("disambiguation", sa.String(300), nullable=True),
        sa.Column("aliases", sa.JSON, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quality_score", sa.REAL, nullable=True),
        sa.Column("embedder", sa.String(100), nullable=False),
        sa.Column("source_endpoint", sa.String(500), nullable=True),
        sa.Column("pack_id", sa.String(100), nullable=True),
        sa.Column("local_performer_id", sa.Integer, nullable=True),
        sa.Column("image_url", sa.String(1000), nullable=True),
        sa.Column("extra_endpoints", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_stashdb_ref_stashdb_id", "stashdb_performer_refs", ["stashdb_id"])
    op.create_index("ix_stashdb_ref_pack", "stashdb_performer_refs", ["pack_id"])
    op.create_index("ix_stashdb_ref_name", "stashdb_performer_refs", ["name"])
    op.create_index(
        "ix_stashdb_ref_local_perf",
        "stashdb_performer_refs",
        ["local_performer_id"],
        postgresql_where=sa.text("local_performer_id IS NOT NULL"),
    )
    op.create_index(
        "ix_stashdb_ref_endpoint",
        "stashdb_performer_refs",
        ["source_endpoint"],
        postgresql_where=sa.text("source_endpoint IS NOT NULL"),
    )
    # Centroid vector column via raw SQL (pgvector).
    # No HNSW index — sequential scan is fast enough at this scale.
    op.execute("ALTER TABLE stashdb_performer_refs ADD COLUMN centroid vector(512) NOT NULL")

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
        sa.Column("stashdb_match_id", sa.Integer, sa.ForeignKey("stashdb_performer_refs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("stashdb_match_score", sa.REAL, nullable=True),
        sa.Column("stashdb_suggestion_rejected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rejected_performer_ids", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_face_clusters_status", "face_clusters", ["status"])
    op.create_index("ix_face_clusters_performer", "face_clusters", ["performer_id"])
    op.create_index(
        "ix_face_clusters_stashdb",
        "face_clusters",
        ["stashdb_match_id"],
        postgresql_where=sa.text("stashdb_match_id IS NOT NULL"),
    )
    # Partial index for unmatched-cluster backfill queries
    op.execute(
        "CREATE INDEX ix_face_clusters_unmatched ON face_clusters (status) "
        "WHERE status = 'unidentified' AND stashdb_match_id IS NULL"
    )
    # Centroid vector column + HNSW index via raw SQL (pgvector)
    op.execute("ALTER TABLE face_clusters ADD COLUMN centroid vector(512)")
    op.execute(
        "CREATE INDEX ix_face_clusters_centroid ON face_clusters "
        "USING hnsw (centroid vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # ── detection_tracks.cluster_id (deferred FK) ─────────────────────
    # Added here because face_clusters must exist before the FK.
    op.add_column(
        "detection_tracks",
        sa.Column(
            "cluster_id", sa.Integer,
            sa.ForeignKey("face_clusters.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_det_tracks_cluster", "detection_tracks", ["cluster_id"],
        postgresql_where=sa.text("cluster_id IS NOT NULL"),
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
    op.create_index("ix_face_emb_cluster_entity", "face_embeddings", ["cluster_id", "entity_type", "entity_id"])
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

    # ── face_performer_assignments ────────────────────────────────────
    op.create_table(
        "face_performer_assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("performer_id", sa.Integer, nullable=False),
        sa.Column("cluster_id", sa.Integer, sa.ForeignKey("face_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "entity_type", "entity_id", "performer_id", "cluster_id",
            name="uq_face_performer_assignment",
        ),
    )
    op.create_index("ix_fpa_entity_performer", "face_performer_assignments", ["entity_type", "entity_id", "performer_id"])
    op.create_index("ix_fpa_cluster", "face_performer_assignments", ["cluster_id"])


def downgrade() -> None:
    op.drop_table("face_performer_assignments")
    op.drop_table("face_embeddings")
    op.drop_table("face_clusters")
    op.drop_table("stashdb_performer_refs")
    op.drop_table("detection_tracks")
