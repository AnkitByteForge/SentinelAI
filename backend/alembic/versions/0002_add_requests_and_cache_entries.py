"""add requests and cache_entries tables, plus the cache HNSW index

Mirrors app.db.models.RequestLog / CacheEntry exactly. These two tables
were previously created ad hoc by Base.metadata.create_all() in
app/db/database.py's init_db(), and the HNSW index by a standalone
script (backend/create_hnsw_index.py) that had to be run manually after
the tables existed. Both are folded into this migration so a single
`alembic upgrade head` produces the complete, fully-indexed schema.

Revision ID: 0002_requests_cache
Revises: 0001_api_keys
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "0002_requests_cache"
down_revision: Union[str, None] = "0001_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "requests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("error_type", sa.String(), nullable=True),
        sa.Column("fallback_from", sa.String(), nullable=True),
        sa.Column("prompt_preview", sa.Text(), nullable=True),
        sa.Column("response_preview", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "cache_entries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("prompt_hash", sa.String(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=True),
        sa.Column("saved_cost_usd", sa.Float(), nullable=True),
        sa.Column("is_stale", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cache_entries_prompt_hash", "cache_entries", ["prompt_hash"], unique=True)

    # HNSW index for cosine distance (<=> operator).
    # m=16: connections per layer (higher = better recall, more memory)
    # ef_construction=64: search depth during build (higher = better quality, slower build)
    op.execute("""
        CREATE INDEX IF NOT EXISTS cache_embedding_hnsw
        ON cache_entries
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS cache_embedding_hnsw")
    op.drop_index("ix_cache_entries_prompt_hash", table_name="cache_entries")
    op.drop_table("cache_entries")
    op.drop_table("requests")
