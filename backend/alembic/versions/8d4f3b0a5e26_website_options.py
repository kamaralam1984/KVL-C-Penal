"""website site tools options (php settings, redirects, protected dirs, ip deny, hotlink)

Revision ID: 8d4f3b0a5e26
Revises: 7c3e2a9d4f15
Create Date: 2026-11-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8d4f3b0a5e26"
down_revision: Union[str, Sequence[str], None] = "7c3e2a9d4f15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("websites", sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("websites", "options")
