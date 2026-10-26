"""user hosts and audit log

Revision ID: d3a91b7c2e10
Revises: c7f4c281255d
Create Date: 2026-10-26 21:00:00.000000

Adds user_hosts (several hostmasks per user) and audit_log (admin actions).
Safe on databases where the bot already created these tables at startup.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a91b7c2e10"
down_revision: Union[str, Sequence[str], None] = "c7f4c281255d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("user_hosts"):
        op.create_table(
            "user_hosts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("mask", sa.String(length=256), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_user_hosts_user_id"), "user_hosts", ["user_id"], unique=False)
    if not _has_table("audit_log"):
        op.create_table(
            "audit_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("actor", sa.String(length=256), nullable=False),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("target", sa.String(length=256), nullable=False),
            sa.Column("details", sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_log_at"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_user_hosts_user_id"), table_name="user_hosts")
    op.drop_table("user_hosts")
