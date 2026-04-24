"""add thread columns to messages

Revision ID: a1b2c3d4e5f6
Revises: 4293e3599ded
Create Date: 2026-04-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "4293e3599ded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("thread_id", sa.UUID(), nullable=True))
    op.add_column("messages", sa.Column("thread_title", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("thread_resolved_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_messages_thread_id",
        "messages",
        "messages",
        ["thread_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_thread_id", table_name="messages")
    op.drop_constraint("fk_messages_thread_id", "messages", type_="foreignkey")
    op.drop_column("messages", "thread_resolved_at")
    op.drop_column("messages", "thread_title")
    op.drop_column("messages", "thread_id")
