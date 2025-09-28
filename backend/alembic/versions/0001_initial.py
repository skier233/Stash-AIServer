"""Initial consolidated schema (authoritative baseline)

This replaces the earlier incremental chain (legacy files 0001-0006). It is
aligned with the current ORM models in `app.models.interaction`.

NO ai_requests table (feature removed).

If you have an existing dev DB that previously ran old migrations, either
rebuild it fresh or manually stamp:
    alembic stamp 0001_initial
after ensuring tables match these definitions.
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:  # noqa: D401
    # task_history (lightweight execution log) — keep minimal needed fields
    op.create_table(
        'task_history',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('task_id', sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column('action_id', sa.String(length=200), nullable=False),
        sa.Column('service', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('started_at', sa.Float, nullable=True),
        sa.Column('finished_at', sa.Float, nullable=True),
        sa.Column('submitted_at', sa.Float, nullable=False),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('items_sent', sa.Integer, nullable=True),
        sa.Column('item_id', sa.String(length=200), nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_task_history_task_id', 'task_history', ['task_id'])
    op.create_index('ix_task_history_created_at', 'task_history', ['created_at'])
    # High-value composite for filtered history queries (service/status + recency)
    op.create_index('ix_task_history_service_status_created', 'task_history', ['service', 'status', 'created_at'])

    # interaction_events (raw immutable stream)
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
    op.create_index('ix_interaction_session_scene', 'interaction_events', ['session_id', 'entity_type', 'entity_id'])
    op.create_index('ix_interaction_client_ts', 'interaction_events', ['client_ts'])
    op.create_index('ix_interaction_events_event_type', 'interaction_events', ['event_type'])
    op.create_index('ix_interaction_events_entity_id', 'interaction_events', ['entity_id'])
    # Composite covering common windowed scene queries (ordered by client_ts)
    op.create_index(
        'ix_interaction_events_session_entity_ts',
        'interaction_events',
        ['session_id', 'entity_type', 'entity_id', 'client_ts']
    )

    # interaction_sessions (current active / historical sessions)
    op.create_table(
        'interaction_sessions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('last_event_ts', sa.DateTime, nullable=False),
        sa.Column('session_start_ts', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_entity_type', sa.String(length=30), nullable=True),
        sa.Column('last_entity_id', sa.String(length=64), nullable=True),
        sa.Column('last_entity_event_ts', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('client_fingerprint', sa.String(length=128), nullable=True),
        sa.Column('ended_at', sa.DateTime, nullable=True),
    )
    op.create_unique_constraint('uq_interaction_session_id', 'interaction_sessions', ['session_id'])
    op.create_index('ix_interaction_sessions_session_id', 'interaction_sessions', ['session_id'])
    op.create_index('ix_interaction_sessions_ended_at', 'interaction_sessions', ['ended_at'])
    op.create_index('ix_interaction_sessions_client_fingerprint', 'interaction_sessions', ['client_fingerprint'])
    # Composite to accelerate fingerprint-based merge/finalization lookups
    op.create_index('ix_interaction_sessions_fp_ended_last', 'interaction_sessions', ['client_fingerprint', 'ended_at', 'last_event_ts'])

    # session alias mapping
    op.create_table(
        'interaction_session_aliases',
        sa.Column('alias_session_id', sa.String(length=64), primary_key=True),
        sa.Column('canonical_session_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_interaction_session_aliases_canonical', 'interaction_session_aliases', ['canonical_session_id'])

    # per-session per-scene aggregation
    op.create_table(
        'scene_watch',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('scene_id', sa.String(length=64), nullable=False),
        sa.Column('page_entered_at', sa.DateTime, nullable=False),
        sa.Column('page_left_at', sa.DateTime, nullable=True),
        sa.Column('total_watched_s', sa.Float, nullable=False, server_default='0'),
        sa.Column('watch_percent', sa.Float, nullable=True),
        sa.Column('last_processed_event_ts', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_scene_watch_session_id', 'scene_watch', ['session_id'])
    op.create_index('ix_scene_watch_scene_id', 'scene_watch', ['scene_id'])
    op.create_index('ix_scene_watch_session_scene', 'scene_watch', ['session_id', 'scene_id'])

    # fine-grained watch segments
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
    # Composite for ordered segment scans per (session, scene)
    op.create_index('ix_scene_watch_segments_sess_scene_start', 'scene_watch_segments', ['session_id', 'scene_id', 'start_s'])

    # aggregated scene stats
    op.create_table(
        'scene_derived',
        sa.Column('scene_id', sa.String(length=64), primary_key=True),
        sa.Column('last_viewed_at', sa.DateTime, nullable=True),
        sa.Column('derived_o_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer, nullable=False, server_default='0'),
    )

    # aggregated image stats
    op.create_table(
        'image_derived',
        sa.Column('image_id', sa.String(length=64), primary_key=True),
        sa.Column('last_viewed_at', sa.DateTime, nullable=True),
        sa.Column('derived_o_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer, nullable=False, server_default='0'),
    )

    # stored library search/filter events
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
    # Library + time for trending / analytics
    op.create_index('ix_interaction_library_search_library_created', 'interaction_library_search', ['library', 'created_at'])


def downgrade() -> None:  # pragma: no cover - full rollback seldom used
    # Drop added composite indexes (must precede table drops)
    pass