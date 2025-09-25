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
        sa.Column('ts', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('client_ts', sa.DateTime, nullable=False),
        sa.Column('metadata', sa.JSON, nullable=True),
        sa.Column('page_url', sa.String(length=500), nullable=True),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.Column('viewport_w', sa.Integer, nullable=True),
        sa.Column('viewport_h', sa.Integer, nullable=True),
        sa.Column('schema_version', sa.Integer, nullable=False, server_default='1'),
    )
    op.create_unique_constraint('uq_interaction_client_event_id', 'interaction_events', ['client_event_id'])
    op.create_index('ix_interaction_session_scene', 'interaction_events', ['session_id','entity_type','entity_id'])
    op.create_index('ix_interaction_client_ts', 'interaction_events', ['client_ts'])
    op.create_index('ix_interaction_events_session_id', 'interaction_events', ['session_id'])

    op.create_table(
        'interaction_sessions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('last_event_ts', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_scene_id', sa.String(length=64), nullable=True),
        sa.Column('last_scene_event_ts', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_unique_constraint('uq_interaction_session_id', 'interaction_sessions', ['session_id'])
    op.create_index('ix_interaction_sessions_session_id', 'interaction_sessions', ['session_id'])

    op.create_table(
        'scene_watch_summaries',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('scene_id', sa.String(length=64), nullable=False),
        sa.Column('total_watched_s', sa.Float, nullable=False, server_default='0'),
        sa.Column('duration_s', sa.Float, nullable=True),
        sa.Column('percent_watched', sa.Float, nullable=True),
        sa.Column('completed', sa.Integer, nullable=False, server_default='0'),
        sa.Column('segments', sa.JSON, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_unique_constraint('uq_scene_watch_session_scene', 'scene_watch_summaries', ['session_id','scene_id'])
    op.create_index('ix_scene_watch_session_id', 'scene_watch_summaries', ['session_id'])
    op.create_index('ix_scene_watch_scene_id', 'scene_watch_summaries', ['scene_id'])


def downgrade() -> None:
    op.drop_index('ix_scene_watch_scene_id', table_name='scene_watch_summaries')
    op.drop_index('ix_scene_watch_session_id', table_name='scene_watch_summaries')
    op.drop_constraint('uq_scene_watch_session_scene', 'scene_watch_summaries')
    op.drop_table('scene_watch_summaries')

    op.drop_index('ix_interaction_sessions_session_id', table_name='interaction_sessions')
    op.drop_constraint('uq_interaction_session_id', 'interaction_sessions')
    op.drop_table('interaction_sessions')

    op.drop_index('ix_interaction_events_session_id', table_name='interaction_events')
    op.drop_index('ix_interaction_client_ts', table_name='interaction_events')
    op.drop_index('ix_interaction_session_scene', table_name='interaction_events')
    op.drop_constraint('uq_interaction_client_event_id', 'interaction_events')
    op.drop_table('interaction_events')
