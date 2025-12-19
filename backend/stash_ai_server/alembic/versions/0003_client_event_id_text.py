"""client_event_id to text

Revision ID: 0003_client_event_id_text
Revises: 0002_recommendation_preferences
Create Date: 2025-12-18
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0003_client_event_id_text'
down_revision = '0002_recommendation_preferences'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Allow alphanumeric client event ids (e.g., "evt_...")
    op.alter_column(
        'interaction_events',
        'client_event_id',
        existing_type=sa.Integer(),
        type_=sa.String(length=191),
        existing_nullable=True,
        postgresql_using='client_event_id::text'
    )


def downgrade() -> None:
    # Best-effort: cast numeric strings back to int, else NULL
    op.alter_column(
        'interaction_events',
        'client_event_id',
        existing_type=sa.String(length=191),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="CASE WHEN client_event_id ~ '^[0-9]+$' THEN client_event_id::integer ELSE NULL END"
    )
