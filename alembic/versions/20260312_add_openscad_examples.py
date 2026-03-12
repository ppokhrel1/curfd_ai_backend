"""add openscad_examples table with vector embeddings for RAG

Revision ID: 20260312_add_openscad_examples
Revises: 20260308_add_missing_indexes
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260312_add_openscad_examples"
down_revision = "20260308_add_missing_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension (already available in Supabase)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "openscad_examples",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("source", sa.String(100), nullable=False, server_default="huggingface"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Add vector column (3072 dims = Gemini gemini-embedding-001)
    op.execute("ALTER TABLE openscad_examples ADD COLUMN embedding vector(3072)")

    op.create_index("ix_openscad_examples_category", "openscad_examples", ["category"])
    # No vector index needed for ~300 rows — sequential scan is fast enough


def downgrade() -> None:
    op.drop_index("ix_openscad_examples_category", table_name="openscad_examples")
    op.drop_table("openscad_examples")
