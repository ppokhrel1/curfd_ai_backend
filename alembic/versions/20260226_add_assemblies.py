"""add assemblies table

Revision ID: 20260226_add_assemblies
Revises: 20260209_add_session_name
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260226_add_assemblies"
down_revision = "20260209_add_session_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assemblies",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("chat_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("parts", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("assemblies")
