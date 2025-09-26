"""interactions tables

Revision ID: 0003_interactions
Revises: 0002_task_history
Create Date: 2025-09-25
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0003_interactions'
down_revision = '0002_task_history'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'interaction_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('client_event_id', sa.String(length=64), nullable=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.String(length=64), nullable=False),
        sa.Column('client_ts', sa.DateTime, nullable=False),
        sa.Column('metadata', sa.JSON, nullable=True),
    )
    op.create_unique_constraint('uq_interaction_client_event_id', 'interaction_events', ['client_event_id'])
    op.create_index('ix_interaction_session_scene', 'interaction_events', ['session_id','entity_type','entity_id'])
    op.create_index('ix_interaction_client_ts', 'interaction_events', ['client_ts'])

    op.create_table(
        'interaction_sessions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('last_event_ts', sa.DateTime, nullable=False),
        sa.Column('session_start_ts', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    # last_scene_id and last_scene_event_ts removed; use last_entity_* instead
        sa.Column('last_entity_type', sa.String(length=30), nullable=True),
        sa.Column('last_entity_id', sa.String(length=64), nullable=True),
        sa.Column('last_entity_event_ts', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('client_fingerprint', sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint('uq_interaction_session_id', 'interaction_sessions', ['session_id'])
    op.create_index('ix_interaction_sessions_session_id', 'interaction_sessions', ['session_id'])

    # Create a per-session-per-scene page visit record. This allows linking segments
    # to a specific page visit (scene_watch) and prevents duplicate segment inserts.
    op.create_table(
        'scene_watch',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('scene_id', sa.String(length=64), nullable=False),
        sa.Column('page_entered_at', sa.DateTime, nullable=False),
        sa.Column('page_left_at', sa.DateTime, nullable=True),
        sa.Column('total_watched_s', sa.Float, nullable=False, server_default='0'),
        sa.Column('watch_percent', sa.Float, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_scene_watch_session_id', 'scene_watch', ['session_id'])
    op.create_index('ix_scene_watch_scene_id', 'scene_watch', ['scene_id'])

    op.create_table(
        'scene_watch_segments',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('scene_watch_id', sa.Integer, nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('scene_id', sa.String(length=64), nullable=False),
        sa.Column('start_s', sa.Float, nullable=False),
        sa.Column('end_s', sa.Float, nullable=False),
        sa.Column('watched_s', sa.Float, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_scene_watch_segments_scene_watch_id', 'scene_watch_segments', ['scene_watch_id'])
    op.create_index('ix_scene_watch_segments_session_id', 'scene_watch_segments', ['session_id'])
    op.create_index('ix_scene_watch_segments_scene_id', 'scene_watch_segments', ['scene_id'])

    op.create_table(
        'scene_derived',
        sa.Column('scene_id', sa.String(length=64), primary_key=True),
        sa.Column('last_viewed_at', sa.DateTime, nullable=True),
        sa.Column('derived_o_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer, nullable=False, server_default='0'),
    )
    op.create_table(
        'image_derived',
        sa.Column('image_id', sa.String(length=64), primary_key=True),
        sa.Column('last_viewed_at', sa.DateTime, nullable=True),
        sa.Column('derived_o_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer, nullable=False, server_default='0'),
    )
    op.create_table(
        'interaction_library_search',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('library', sa.String(length=20), nullable=False),
        sa.Column('query', sa.String(length=512), nullable=True),
        sa.Column('filters', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_interaction_library_search_session_id', 'interaction_library_search', ['session_id'])


def downgrade() -> None:
    # downgrade intentionally left empty (irreversible in-place migration for tests/dev)
    pass
