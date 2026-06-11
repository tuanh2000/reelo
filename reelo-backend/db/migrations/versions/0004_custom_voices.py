"""add custom_voices (shared OmniVoice voice-clone library)

Revision ID: 0004_custom_voices
Revises: 0003_user_settings
Create Date: 2026-06-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_custom_voices"
down_revision: Union[str, None] = "0003_user_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_voices",
        sa.Column("id", sa.String(length=64), nullable=False),
        # SET NULL (not CASCADE): a deleted account leaves its shared voices in
        # the cross-tenant library, just orphaned.
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("audio_key", sa.String(length=512), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_custom_voices_created_by_user_id", "custom_voices", ["created_by_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_custom_voices_created_by_user_id", table_name="custom_voices")
    op.drop_table("custom_voices")
