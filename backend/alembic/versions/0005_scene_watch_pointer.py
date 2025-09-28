"""add last_processed_event_ts to scene_watch

Revision ID: 0005_scene_watch_pointer
Revises: 0004_session_aliases
Create Date: 2025-09-28
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0005_scene_watch_pointer'
down_revision = '0004_session_aliases'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('scene_watch', sa.Column('last_processed_event_ts', sa.DateTime(), nullable=True))
    op.add_column('scene_watch', sa.Column('processed_version', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_scene_watch_session_scene', 'scene_watch', ['session_id', 'scene_id'])


def downgrade() -> None:
    # irreversible for now
    pass
