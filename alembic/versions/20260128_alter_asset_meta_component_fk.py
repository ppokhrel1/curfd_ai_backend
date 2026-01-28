"""make asset_meta.component_of a strict FK

Revision ID: 20260128_alter_asset_meta_component_fk
Revises: 20260128_add_asset_meta
Create Date: 2026-01-28
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260128_alter_asset_meta_component_fk"
down_revision = "20260128_add_asset_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("asset_meta") as batch_op:
        batch_op.alter_column("component_of", existing_type=sa.String(length=255), type_=sa.String(length=36))
        batch_op.create_foreign_key(
            "fk_asset_meta_component_of", "assets", ["component_of"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("asset_meta") as batch_op:
        batch_op.drop_constraint("fk_asset_meta_component_of", type_="foreignkey")
        batch_op.alter_column("component_of", existing_type=sa.String(length=36), type_=sa.String(length=255))
