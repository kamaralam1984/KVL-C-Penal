"""email outbox delivery tracking (SMTP transport)

Revision ID: 5a1c0e7d2b91
Revises: 041f68a04c4d
Create Date: 2026-11-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5a1c0e7d2b91"
down_revision: Union[str, Sequence[str], None] = "041f68a04c4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_outbox", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("email_outbox", sa.Column("last_error", sa.String(length=300), nullable=True))
    op.add_column("email_outbox", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_outbox", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_email_outbox_status_next", "email_outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_status_next", table_name="email_outbox")
    op.drop_column("email_outbox", "sent_at")
    op.drop_column("email_outbox", "next_attempt_at")
    op.drop_column("email_outbox", "last_error")
    op.drop_column("email_outbox", "attempts")
