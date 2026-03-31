"""entity_ratings table

Revision ID: 0007_entity_ratings
Revises: 0006_task_history_column_types
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_entity_ratings"
down_revision = "0006_task_history_column_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_ratings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(200), nullable=False),
        sa.Column("rating_key", sa.String(100), nullable=False, server_default="default"),
        sa.Column("value", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("entity_type", "entity_id", "rating_key", name="uq_entity_rating"),
        sa.Index("ix_entity_ratings_entity", "entity_type", "entity_id"),
    )


def downgrade() -> None:
    op.drop_table("entity_ratings")
