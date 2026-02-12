"""add name field to sessions

Revision ID: 20260209_add_session_name
Revises: 20260209_drop_user_foreign_keys_for_supabase_auth
Create Date: 2026-02-09
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260209_add_session_name"
down_revision = "20260209_drop_user_foreign_keys_for_supabase_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("name")
