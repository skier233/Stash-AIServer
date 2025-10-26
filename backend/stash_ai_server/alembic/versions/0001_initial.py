"""Initial consolidated schema (authoritative baseline)

This replaces the earlier incremental chain (legacy files 0001-0006). It is
aligned with the current ORM models in `stash_ai_server.models.interaction`.


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
    # plugin_meta (tracks simple plugin state)
    op.create_table(
        'plugin_meta',
        sa.Column('id', sa.Integer, primary_key=True),
        # Remove explicit index=True; unique constraint already creates an index
        sa.Column('name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('version', sa.String(length=50), nullable=False),
        sa.Column('required_backend', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='active'),
        sa.Column('migration_head', sa.String(length=100), nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('human_name', sa.String(length=150), nullable=True),
        sa.Column('server_link', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    # plugin_sources (remote registries) / plugin_catalog (available plugins) / plugin_settings (persisted config)
    op.create_table(
        'plugin_sources',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.text('1')),
        sa.Column('last_refreshed_at', sa.DateTime, nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    op.create_table(
        'plugin_catalog',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('source_id', sa.Integer, sa.ForeignKey('plugin_sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plugin_name', sa.String(length=100), nullable=False),
        sa.Column('version', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('human_name', sa.String(length=150), nullable=True),
        sa.Column('server_link', sa.String(length=500), nullable=True),
        sa.Column('dependencies_json', sa.JSON, nullable=True),
        sa.Column('manifest_json', sa.JSON, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_plugin_catalog_source_id', 'plugin_catalog', ['source_id'])
    op.create_index('ix_plugin_catalog_plugin_name', 'plugin_catalog', ['plugin_name'])

    op.create_table(
        'plugin_settings',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('plugin_name', sa.String(length=100), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False, server_default='string'),
        sa.Column('label', sa.String(length=150), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('default_value', sa.JSON, nullable=True),
        sa.Column('options', sa.JSON, nullable=True),
        sa.Column('value', sa.JSON, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_plugin_settings_plugin_name', 'plugin_settings', ['plugin_name'])
    op.create_index('ix_plugin_settings_plugin_name_key', 'plugin_settings', ['plugin_name', 'key'])
    # task_history (lightweight execution log) — keep minimal needed fields
    op.create_table(
        'task_history',
        sa.Column('id', sa.Integer, primary_key=True),
        # unique already implies an index; remove extra index=True
        sa.Column('task_id', sa.Integer, nullable=False, unique=True),
        sa.Column('action_id', sa.Integer, nullable=False),
        sa.Column('service', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('started_at', sa.Float, nullable=True),
        sa.Column('finished_at', sa.Float, nullable=True),
        sa.Column('submitted_at', sa.Float, nullable=False),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('items_sent', sa.Integer, nullable=True),
        sa.Column('item_id', sa.Integer, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_task_history_created_at', 'task_history', ['created_at'])
    # High-value composite for filtered history queries (service/status + recency)
    op.create_index('ix_task_history_service_status_created', 'task_history', ['service', 'status', 'created_at'])

    # interaction_events (raw immutable stream)
    op.create_table(
        'interaction_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('client_event_id', sa.Integer, nullable=True),
        sa.Column('session_id', sa.String(length=128), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.Integer, nullable=False),
        sa.Column('client_ts', sa.DateTime, nullable=False),
        sa.Column('metadata', sa.JSON, nullable=True),
        sa.UniqueConstraint('client_event_id', name='uq_interaction_client_event_id')
    )
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
        sa.Column('session_id', sa.String(length=128), nullable=False),
        sa.Column('last_event_ts', sa.DateTime, nullable=False),
        sa.Column('session_start_ts', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_entity_type', sa.String(length=30), nullable=True),
        sa.Column('last_entity_id', sa.Integer, nullable=True),
        sa.Column('last_entity_event_ts', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('client_fingerprint', sa.String(length=128), nullable=True),
        sa.Column('ended_at', sa.DateTime, nullable=True),
        sa.UniqueConstraint('session_id', name='uq_interaction_session_id')
    )
    op.create_index('ix_interaction_sessions_session_id', 'interaction_sessions', ['session_id'])
    op.create_index('ix_interaction_sessions_ended_at', 'interaction_sessions', ['ended_at'])
    op.create_index('ix_interaction_sessions_client_fingerprint', 'interaction_sessions', ['client_fingerprint'])
    # Composite to accelerate fingerprint-based merge/finalization lookups
    op.create_index('ix_interaction_sessions_fp_ended_last', 'interaction_sessions', ['client_fingerprint', 'ended_at', 'last_event_ts'])

    # session alias mapping
    op.create_table(
        'interaction_session_aliases',
        sa.Column('alias_session_id', sa.String(length=128), primary_key=True),
        sa.Column('canonical_session_id', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_interaction_session_aliases_canonical', 'interaction_session_aliases', ['canonical_session_id'])

    # per-session per-scene aggregation
    op.create_table(
        'scene_watch',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=128), nullable=False),
        sa.Column('scene_id', sa.Integer, nullable=False),
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
        sa.Column('session_id', sa.String(length=128), nullable=False),
        sa.Column('scene_id', sa.Integer, nullable=False),
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
        sa.Column('scene_id', sa.Integer, primary_key=True),
        sa.Column('last_viewed_at', sa.DateTime, nullable=True),
        sa.Column('derived_o_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer, nullable=False, server_default='0'),
    )

    # aggregated image stats
    op.create_table(
        'image_derived',
    sa.Column('image_id', sa.Integer, primary_key=True),
        sa.Column('last_viewed_at', sa.DateTime, nullable=True),
        sa.Column('derived_o_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer, nullable=False, server_default='0'),
    )

    # stored library search/filter events
    op.create_table(
        'interaction_library_search',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('session_id', sa.String(length=128), nullable=False),
        sa.Column('library', sa.String(length=20), nullable=False),
        sa.Column('query', sa.String(length=512), nullable=True),
        sa.Column('filters', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_interaction_library_search_session_id', 'interaction_library_search', ['session_id'])
    # Library + time for trending / analytics
    op.create_index('ix_interaction_library_search_library_created', 'interaction_library_search', ['library', 'created_at'])

    # ------------------------------------------------------------------
    # AI model catalog and result storage (shared across plugins)
    # ------------------------------------------------------------------
    op.create_table(
        'ai_models',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('service', sa.String(length=100), nullable=False),
        sa.Column('plugin_name', sa.String(length=100), nullable=True),
        sa.Column('model_id', sa.Integer, nullable=True),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('version', sa.Float, nullable=True),
        sa.Column('model_type', sa.String(length=50), nullable=True),
        sa.Column('categories', sa.JSON, nullable=True),
        sa.Column('extra', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    sa.UniqueConstraint('service', 'model_id', 'name', name='uq_ai_model_service_model_name'),
    )
    op.create_index('ix_ai_models_service', 'ai_models', ['service'])

    op.create_table(
        'ai_model_runs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('service', sa.String(length=100), nullable=False),
        sa.Column('plugin_name', sa.String(length=100), nullable=True),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('entity_id', sa.Integer, nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='completed'),
        sa.Column('input_params', sa.JSON, nullable=True),
        sa.Column('started_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('result_metadata', sa.JSON, nullable=True),
    )
    op.create_index('ix_ai_model_runs_entity', 'ai_model_runs', ['entity_type', 'entity_id'])
    op.create_index('ix_ai_model_runs_service_entity', 'ai_model_runs', ['service', 'entity_type', 'entity_id'])

    op.create_table(
        'ai_model_run_models',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('run_id', sa.Integer, sa.ForeignKey('ai_model_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('model_id', sa.Integer, sa.ForeignKey('ai_models.id', ondelete='SET NULL'), nullable=True),
        sa.Column('input_params', sa.JSON, nullable=True),
        sa.Column('frame_interval', sa.Float, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_ai_run_models_run', 'ai_model_run_models', ['run_id'])
    op.create_index('ix_ai_run_models_model', 'ai_model_run_models', ['model_id'])

    op.create_table(
        'ai_result_timespans',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('run_id', sa.Integer, sa.ForeignKey('ai_model_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('entity_id', sa.Integer, nullable=False),
        sa.Column('payload_type', sa.String(length=50), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('str_value', sa.String(length=150), nullable=True),
        sa.Column('value_id', sa.Integer, nullable=True),
        sa.Column('start_s', sa.Float, nullable=False),
        sa.Column('end_s', sa.Float, nullable=True),
        sa.Column('value_json', sa.JSON, nullable=True),
    )
    op.create_index('ix_ai_timespans_entity', 'ai_result_timespans', ['entity_type', 'entity_id'])
    op.create_index('ix_ai_timespans_run', 'ai_result_timespans', ['run_id'])
    op.create_index('ix_ai_timespans_payload', 'ai_result_timespans', ['payload_type', 'category', 'str_value'])
    op.create_index('ix_ai_timespans_start', 'ai_result_timespans', ['entity_type', 'entity_id', 'start_s'])

    op.create_table(
        'ai_result_aggregates',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('run_id', sa.Integer, sa.ForeignKey('ai_model_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('entity_id', sa.Integer, nullable=False),
        sa.Column('payload_type', sa.String(length=50), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('str_value', sa.String(length=150), nullable=True),
        sa.Column('value_id', sa.Integer, nullable=True),
        sa.Column('metric', sa.String(length=50), nullable=False),
        sa.Column('value_float', sa.Float, nullable=True),
        sa.Column('value_json', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_ai_aggregates_entity', 'ai_result_aggregates', ['entity_type', 'entity_id'])
    op.create_index('ix_ai_aggregates_payload', 'ai_result_aggregates', ['payload_type', 'str_value', 'metric'])


def downgrade() -> None:  # pragma: no cover - full rollback seldom used
    # Drop added composite indexes (must precede table drops)
    pass