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
    # Add columns as nullable first to avoid breaking existing rows.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("username", sa.String(length=150), nullable=True))
        batch_op.add_column(sa.Column("hashed_password", sa.String(length=255), nullable=True))

    # Backfill values for existing users.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, email FROM users")).mappings().all()

    used_usernames: set[str] = set()
    for row in rows:
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
                "UPDATE users SET username = :username, hashed_password = :hashed_password WHERE id = :id"
            ),
            {"username": candidate, "hashed_password": hashed, "id": user_id},
        )

    # Enforce NOT NULL + unique for username after backfill.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(length=150), nullable=False)
        batch_op.alter_column(
            "hashed_password", existing_type=sa.String(length=255), nullable=False
        )
        batch_op.create_unique_constraint("uq_users_username", ["username"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_username", type_="unique")
        batch_op.drop_column("hashed_password")
        batch_op.drop_column("username")
