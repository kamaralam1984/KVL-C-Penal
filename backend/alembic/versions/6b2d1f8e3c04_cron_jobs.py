"""customer cron jobs (scheduled https requests)

Revision ID: 6b2d1f8e3c04
Revises: 5a1c0e7d2b91
Create Date: 2026-11-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6b2d1f8e3c04"
down_revision: Union[str, Sequence[str], None] = "5a1c0e7d2b91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cron_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("website_id", sa.Uuid(), sa.ForeignKey("websites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("schedule", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(20)),
        sa.Column("last_http_code", sa.Integer()),
        sa.Column("last_error", sa.String(300)),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("disabled_reason", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cron_jobs_tenant_id", "cron_jobs", ["tenant_id"])
    op.create_index("ix_cron_jobs_website_id", "cron_jobs", ["website_id"])
    op.create_index("ix_cron_jobs_next_run_at", "cron_jobs", ["next_run_at"])
    op.create_index("ix_cron_jobs_due", "cron_jobs", ["enabled", "next_run_at"])
    op.create_table(
        "cron_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("cron_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("http_code", sa.Integer()),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(300)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cron_runs_job_id", "cron_runs", ["job_id"])
    op.create_index("ix_cron_runs_tenant_id", "cron_runs", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("cron_runs")
    op.drop_table("cron_jobs")
