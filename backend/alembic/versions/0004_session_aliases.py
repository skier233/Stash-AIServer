"""session alias table

Revision ID: 0004_session_aliases
Revises: 0003_interactions
Create Date: 2025-09-26
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0004_session_aliases'
down_revision = '0003_interactions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'interaction_session_aliases',
        sa.Column('alias_session_id', sa.String(length=64), primary_key=True),
        sa.Column('canonical_session_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_interaction_session_aliases_canonical', 'interaction_session_aliases', ['canonical_session_id'])


def downgrade() -> None:
    # irreversible for now
    pass
