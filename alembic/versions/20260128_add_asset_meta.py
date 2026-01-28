"""add asset_meta table

Revision ID: 20260128_add_asset_meta
Revises: 20260128_add_revoked_tokens
Create Date: 2026-01-28
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260128_add_asset_meta"
down_revision = "20260128_add_revoked_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_meta",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("part_name", sa.String(length=255), nullable=True),
        sa.Column("component_of", sa.String(length=255), nullable=True),
        sa.Column("storage_url", sa.String(length=1024), nullable=True),
        sa.Column("position_json", sa.JSON(), nullable=True),
        sa.Column("image_paths_json", sa.JSON(), nullable=True),
        sa.Column("uploaded_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("material_json", sa.JSON(), nullable=True),
        sa.Column("is_composite_of_json", sa.JSON(), nullable=True),
        sa.Column("used_for_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("asset_meta")
