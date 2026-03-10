"""add missing indexes on foreign key and frequently queried columns

Revision ID: 20260308_add_missing_indexes
Revises: 20260226_fix_scad_jobs_timestamps
Create Date: 2026-03-08
"""

from alembic import op

revision = "20260308_add_missing_indexes"
down_revision = "20260226_fix_scad_jobs_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── sessions ──
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])

    # ── chats ──
    op.create_index("idx_chats_session_id", "chats", ["session_id"])

    # ── messages (heaviest table) ──
    op.create_index("idx_messages_chat_id", "messages", ["chat_id"])
    op.create_index(
        "idx_messages_chat_created",
        "messages",
        ["chat_id", "created_at"],
    )

    # ── jobs ──
    op.create_index("idx_jobs_session_id", "jobs", ["session_id"])

    # ── assets ──
    op.create_index("idx_assets_job_id", "assets", ["job_id"])
    op.create_index("idx_assets_type", "assets", ["asset_type"])

    # ── asset_meta ──
    op.create_index("idx_asset_meta_asset_id", "asset_meta", ["asset_id"])
    op.create_index("idx_asset_meta_component_of", "asset_meta", ["component_of"])
    op.create_index("idx_asset_meta_part_name", "asset_meta", ["part_name"])


def downgrade() -> None:
    op.drop_index("idx_asset_meta_part_name", table_name="asset_meta")
    op.drop_index("idx_asset_meta_component_of", table_name="asset_meta")
    op.drop_index("idx_asset_meta_asset_id", table_name="asset_meta")
    op.drop_index("idx_assets_type", table_name="assets")
    op.drop_index("idx_assets_job_id", table_name="assets")
    op.drop_index("idx_jobs_session_id", table_name="jobs")
    op.drop_index("idx_messages_chat_created", table_name="messages")
    op.drop_index("idx_messages_chat_id", table_name="messages")
    op.drop_index("idx_chats_session_id", table_name="chats")
    op.drop_index("idx_sessions_user_id", table_name="sessions")
