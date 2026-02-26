"""fix scad_jobs timestamp columns to use timezone-aware TIMESTAMPTZ

Revision ID: 20260226_fix_scad_jobs_timestamps
Revises: 20260226_add_scad_versions
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260226_fix_scad_jobs_timestamps"
down_revision = "20260226_add_scad_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("started_at", "created_at", "finished_at", "updated_at"):
        op.alter_column(
            "scad_jobs",
            col,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(timezone=False),
            existing_nullable=True,
            postgresql_using=f"{col} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    for col in ("started_at", "created_at", "finished_at", "updated_at"):
        op.alter_column(
            "scad_jobs",
            col,
            type_=sa.DateTime(timezone=False),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=True,
            postgresql_using=f"{col} AT TIME ZONE 'UTC'",
        )
