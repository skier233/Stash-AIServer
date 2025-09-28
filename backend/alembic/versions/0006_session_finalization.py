"""add ended_at to interaction_sessions

Revision ID: 0006_session_finalization
Revises: 0005_scene_watch_pointer
Create Date: 2025-09-28
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0006_session_finalization'
down_revision = '0005_scene_watch_pointer'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('interaction_sessions', sa.Column('ended_at', sa.DateTime(), nullable=True))
    op.create_index('ix_interaction_sessions_ended_at', 'interaction_sessions', ['ended_at'])


def downgrade() -> None:
    # irreversible for now
    pass
