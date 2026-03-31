"""Fix task_history column types: task_id, action_id, item_id should be text.

The initial migration (0001) created these as Integer, but the ORM model
and the task manager write UUID strings (task_id), action name strings
(action_id), and entity-id strings (item_id).  Every insert silently failed
with a DatatypeMismatch error, leaving the table permanently empty.

Revision ID: 0006_task_history_column_types
Revises: 0005_face_recognition
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0006_task_history_column_types'
down_revision = '0005_face_recognition'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'task_history', 'task_id',
        existing_type=sa.Integer(),
        type_=sa.String(64),
        existing_nullable=False,
        postgresql_using='task_id::text',
    )
    op.alter_column(
        'task_history', 'action_id',
        existing_type=sa.Integer(),
        type_=sa.String(200),
        existing_nullable=False,
        postgresql_using='action_id::text',
    )
    op.alter_column(
        'task_history', 'item_id',
        existing_type=sa.Integer(),
        type_=sa.String(200),
        existing_nullable=True,
        postgresql_using='item_id::text',
    )


def downgrade() -> None:
    op.alter_column(
        'task_history', 'item_id',
        existing_type=sa.String(200),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using='CASE WHEN item_id ~ $q$^\\d+$$q$ THEN item_id::integer ELSE NULL END',
    )
    op.alter_column(
        'task_history', 'action_id',
        existing_type=sa.String(200),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using='0',
    )
    op.alter_column(
        'task_history', 'task_id',
        existing_type=sa.String(64),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using='0',
    )
