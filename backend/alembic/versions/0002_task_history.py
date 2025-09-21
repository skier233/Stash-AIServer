"""task history

Revision ID: 0002_task_history
Revises: 0001_initial
Create Date: 2025-09-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002_task_history'
down_revision = '0001_initial'
branch_labels = None
depends_on = None

def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index('ix_task_history_task_id', table_name='task_history')
    op.drop_index('ix_task_history_created_at', table_name='task_history')
    op.drop_table('task_history')
