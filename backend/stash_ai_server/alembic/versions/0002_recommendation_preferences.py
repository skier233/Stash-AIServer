"""Add recommendation preference storage

Revision ID: 0002_recommendation_preferences
Revises: 0001_initial
Create Date: 2025-12-18
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_recommendation_preferences'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'recommendation_preferences',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('context', sa.String(length=64), nullable=False),
        sa.Column('recommender_id', sa.String(length=100), nullable=False),
        sa.Column('config', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('context', name='uq_recommendation_preferences_context'),
    )


def downgrade() -> None:
    op.drop_table('recommendation_preferences')
