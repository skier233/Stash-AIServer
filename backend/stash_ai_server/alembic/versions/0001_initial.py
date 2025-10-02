"""Initial consolidated schema (authoritative baseline)

Copied into package for runtime and wheel inclusion.
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

    # plugin_sources / plugin_catalog / plugin_settings
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

    # task_history
    op.create_table(
        'task_history',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('task_id', sa.String(length=64), nullable=False, unique=True),
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
    op.create_index('ix_task_history_created_at', 'task_history', ['created_at'])

def downgrade() -> None:
    pass
