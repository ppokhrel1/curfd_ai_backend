"""add user auth fields non-destructively

Revision ID: 20260128_add_user_auth_fields
Revises: 20260128_add_user_credentials
Create Date: 2026-01-28
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from passlib.context import CryptContext


# revision identifiers, used by Alembic.
revision = "20260128_add_user_auth_fields"
down_revision = "20260128_add_user_credentials"
branch_labels = None
depends_on = None

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def upgrade() -> None:
    conn = op.get_bind()
    existing_cols = {
        row["name"]
        for row in conn.execute(sa.text("PRAGMA table_info(users)")).mappings().all()
    }

    added_columns = False
    if "username" not in existing_cols or "hashed_password" not in existing_cols:
        # Add columns as nullable first to avoid breaking existing rows.
        with op.batch_alter_table("users") as batch_op:
            if "username" not in existing_cols:
                batch_op.add_column(sa.Column("username", sa.String(length=150), nullable=True))
            if "hashed_password" not in existing_cols:
                batch_op.add_column(
                    sa.Column("hashed_password", sa.String(length=255), nullable=True)
                )
        added_columns = True

    # Backfill values for existing users when fields are missing.
    rows = conn.execute(
        sa.text("SELECT id, email, username, hashed_password FROM users")
    ).mappings().all()

    used_usernames: set[str] = set()
    for row in rows:
        if row["username"] and row["hashed_password"]:
            used_usernames.add(row["username"])
            continue

        user_id = row["id"]
        email = row["email"]
        base = None
        if email and "@" in email:
            base = email.split("@", 1)[0]
        if not base:
            base = f"user_{user_id[:8]}"
        candidate = base
        suffix = 1
        while candidate in used_usernames:
            candidate = f"{base}{suffix}"
            suffix += 1
        used_usernames.add(candidate)

        random_password = str(uuid.uuid4())
        hashed = pwd_context.hash(random_password)

        conn.execute(
            sa.text(
                "UPDATE users SET username = COALESCE(username, :username), "
                "hashed_password = COALESCE(hashed_password, :hashed_password) "
                "WHERE id = :id"
            ),
            {"username": candidate, "hashed_password": hashed, "id": user_id},
        )

    # Enforce NOT NULL when safe and unique constraint if missing.
    nulls = conn.execute(
        sa.text(
            "SELECT COUNT(*) AS cnt FROM users "
            "WHERE username IS NULL OR hashed_password IS NULL"
        )
    ).mappings().first()["cnt"]

    if nulls == 0 and added_columns:
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("username", existing_type=sa.String(length=150), nullable=False)
            batch_op.alter_column(
                "hashed_password", existing_type=sa.String(length=255), nullable=False
            )

    # Create unique index if no unique index exists for username.
    index_rows = conn.execute(sa.text("PRAGMA index_list(users)")).mappings().all()
    has_unique_username = False
    for idx in index_rows:
        if not idx.get("unique"):
            continue
        idx_name = idx["name"]
        cols = conn.execute(
            sa.text(f"PRAGMA index_info('{idx_name}')")
        ).mappings().all()
        if len(cols) == 1 and cols[0].get("name") == "username":
            has_unique_username = True
            break

    if not has_unique_username and ("username" in existing_cols or added_columns):
        with op.batch_alter_table("users") as batch_op:
            batch_op.create_unique_constraint("uq_users_username", ["username"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_username", type_="unique")
        batch_op.drop_column("hashed_password")
        batch_op.drop_column("username")
