"""drop local user foreign keys for supabase auth user ids

Revision ID: 20260209_drop_user_foreign_keys_for_supabase_auth
Revises: 20260128_add_asset_meta_composites
Create Date: 2026-02-09
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260209_drop_user_foreign_keys_for_supabase_auth"
down_revision = "20260128_add_asset_meta_composites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_user_id_fkey")
        op.execute(
            "ALTER TABLE asset_meta DROP CONSTRAINT IF EXISTS asset_meta_uploaded_by_fkey"
        )
    else:
        # Constraint names may vary outside Postgres, so try common names.
        with op.batch_alter_table("sessions") as batch_op:
            try:
                batch_op.drop_constraint("sessions_user_id_fkey", type_="foreignkey")
            except Exception:
                pass
        with op.batch_alter_table("asset_meta") as batch_op:
            try:
                batch_op.drop_constraint("asset_meta_uploaded_by_fkey", type_="foreignkey")
            except Exception:
                pass


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.create_foreign_key("sessions_user_id_fkey", "users", ["user_id"], ["id"])

    with op.batch_alter_table("asset_meta") as batch_op:
        batch_op.create_foreign_key(
            "asset_meta_uploaded_by_fkey", "users", ["uploaded_by"], ["id"]
        )
