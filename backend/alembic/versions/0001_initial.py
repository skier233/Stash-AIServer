"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2025-09-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'ai_requests',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('prompt', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime, nullable=False)
    )


def downgrade() -> None:
    op.drop_table('ai_requests')
