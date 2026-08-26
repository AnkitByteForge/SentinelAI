"""add api_keys table

Revision ID: 0001_add_api_keys
Revises:
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_add_api_keys"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("key_prefix", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rate_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
