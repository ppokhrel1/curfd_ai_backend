"""add scad_versions table

Revision ID: 20260226_add_scad_versions
Revises: 20260226_add_assemblies
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260226_add_scad_versions"
down_revision = "20260226_add_assemblies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scad_versions",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("chat_id", sa.String(), nullable=False, index=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("scad_versions")
