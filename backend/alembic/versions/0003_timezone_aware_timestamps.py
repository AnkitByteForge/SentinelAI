"""make requests.created_at and cache_entries.created_at timezone-aware

Real bug, not just a style nit: services/queries.py's window_to_cutoff()
builds a timezone-aware UTC cutoff (datetime.now(timezone.utc) - delta)
and compares it against these columns in /v1/metrics and /v1/logs. Both
columns were plain DateTime (TIMESTAMP WITHOUT TIME ZONE) — asyncpg
rejects mixing an offset-aware Python datetime with a naive column at
query time ("can't subtract offset-naive and offset-aware datetimes"),
which only surfaces once a real row exists to query, not against an
empty table. api_keys.created_at/last_used were already
DateTime(timezone=True) from the start; this brings the other two
tables in line with that.

Existing naive values are reinterpreted as UTC (correct: every writer —
RequestLog inserts in app/tasks.py, CacheEntry inserts in
services/cache.py — has only ever run with the process clock in UTC).

Revision ID: 0003_tz_aware_timestamps
Revises: 0002_requests_cache
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_tz_aware_timestamps"
down_revision: Union[str, None] = "0002_requests_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE requests ALTER COLUMN created_at "
        "TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC'"
    )
    op.execute(
        "ALTER TABLE cache_entries ALTER COLUMN created_at "
        "TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE requests ALTER COLUMN created_at "
        "TYPE TIMESTAMP USING created_at AT TIME ZONE 'UTC'"
    )
    op.execute(
        "ALTER TABLE cache_entries ALTER COLUMN created_at "
        "TYPE TIMESTAMP USING created_at AT TIME ZONE 'UTC'"
    )
