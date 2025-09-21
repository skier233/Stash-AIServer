"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2025-09-20
"""
from alembic import op  # noqa: F401 (kept for potential future expansion)

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:  # noqa: D401
    """Initial migration intentionally left empty after removing deprecated ai_requests table."""


def downgrade() -> None:  # noqa: D401
    """No downgrade actions; table creation removed pre-release."""
