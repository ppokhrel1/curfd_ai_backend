"""add asset_meta_composites table

Revision ID: 20260128_add_asset_meta_composites
Revises: 20260128_alter_asset_meta_component_fk
Create Date: 2026-01-28
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260128_add_asset_meta_composites"
down_revision = "20260128_alter_asset_meta_component_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_meta_composites",
        sa.Column("asset_meta_id", sa.String(length=36), sa.ForeignKey("asset_meta.id"), primary_key=True),
        sa.Column("component_asset_id", sa.String(length=36), sa.ForeignKey("assets.id"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("asset_meta_composites")
