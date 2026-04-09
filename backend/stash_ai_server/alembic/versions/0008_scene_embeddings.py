"""entity_embeddings table for content-level vector storage

Stores dense vector embeddings (audio, visual) per scene/image for
similarity search and recommendation engines.  Uses pgvector without
a fixed dimension so multiple model families can coexist.

Revision ID: 0008_scene_embeddings
Revises: 0007_entity_ratings
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_scene_embeddings"
down_revision = "0007_entity_ratings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension should already exist from 0005_face_recognition,
    # but ensure it's present in case migrations are run selectively.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create the table using raw SQL so the embedding column is created
    # as pgvector's `vector` type directly (no dimension constraint).
    op.execute("""
        CREATE TABLE entity_embeddings (
            id SERIAL PRIMARY KEY,
            run_id INTEGER REFERENCES ai_model_runs(id) ON DELETE SET NULL,
            entity_type VARCHAR(20) NOT NULL,
            entity_id INTEGER NOT NULL,
            embedding_type VARCHAR(50) NOT NULL,
            embedding vector NOT NULL,
            dim INTEGER NOT NULL,
            embedder VARCHAR(100) NOT NULL,
            norm REAL NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 1,
            start_time REAL,
            end_time REAL,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.create_index("ix_entity_emb_entity", "entity_embeddings", ["entity_type", "entity_id"])
    op.create_index("ix_entity_emb_type", "entity_embeddings", ["embedding_type"])
    op.create_index("ix_entity_emb_run", "entity_embeddings", ["run_id"])
    op.create_unique_constraint(
        "uq_entity_embedding_entity_type_embedder",
        "entity_embeddings",
        ["entity_type", "entity_id", "embedding_type", "embedder"],
    )


def downgrade() -> None:
    op.drop_table("entity_embeddings")
