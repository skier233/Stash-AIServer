"""Add performance indexes for AI tagging and recommendations

Revision ID: 0004_ai_tagging_perf_indexes
Revises: 0003_client_event_id_text
Create Date: 2026-03-18
"""
from alembic import op


revision = '0004_ai_tagging_perf_indexes'
down_revision = '0003_client_event_id_text'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- AI result tables (tagging fast-path) --

    # Covers joins from ai_model_runs into aggregates filtered by payload_type + metric
    # (get_scene_tag_totals, get_image_tag_ids, recommendation queries)
    op.create_index(
        'ix_ai_aggregates_run_payload_metric',
        'ai_result_aggregates',
        ['run_id', 'payload_type', 'metric'],
    )

    # Covers joins from ai_model_runs into timespans filtered by payload_type
    # (get_scene_timespans — called on every scene tagging operation)
    op.create_index(
        'ix_ai_timespans_run_payload',
        'ai_result_timespans',
        ['run_id', 'payload_type'],
    )

    # -- Watch / recommendation tables --

    # Covers _fetch_scene_watch_intervals: WHERE scene_id = ? ORDER BY start_s
    # (recommendation tag-overlap calculations)
    op.create_index(
        'ix_scene_watch_segments_scene_start',
        'scene_watch_segments',
        ['scene_id', 'start_s'],
    )

    # Covers load_watch_history_summary: WHERE page_entered_at >= cutoff
    # (recommendation engine watch-history loader)
    op.create_index(
        'ix_scene_watch_page_entered',
        'scene_watch',
        ['page_entered_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_scene_watch_page_entered', table_name='scene_watch')
    op.drop_index('ix_scene_watch_segments_scene_start', table_name='scene_watch_segments')
    op.drop_index('ix_ai_timespans_run_payload', table_name='ai_result_timespans')
    op.drop_index('ix_ai_aggregates_run_payload_metric', table_name='ai_result_aggregates')
