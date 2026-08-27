"""API key generation, hashing, Redis-cached resolution, rotation, and revocation."""
import pytest

from app.db.database import AsyncSessionLocal
from app.services import api_keys


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


def test_generated_key_has_the_expected_format():
    raw = api_keys._generate_raw_key()
    assert raw.startswith(api_keys.KEY_PREFIX)
    assert len(raw) == len(api_keys.KEY_PREFIX) + api_keys.KEY_RANDOM_HEX_CHARS


def test_hash_is_deterministic_and_sha256():
    raw = "sk-sent-abc123"
    assert api_keys.hash_key(raw) == api_keys.hash_key(raw)
    assert len(api_keys.hash_key(raw)) == 64


def test_raw_key_is_never_recoverable_from_the_hash():
    raw1 = api_keys._generate_raw_key()
    raw2 = api_keys._generate_raw_key()
    assert api_keys.hash_key(raw1) != api_keys.hash_key(raw2)


async def test_create_and_resolve_round_trip(db):
    row, raw_key = await api_keys.create_key(db=db, name="test-key", rate_limit=42)
    assert row.rate_limit == 42
    assert row.is_active is True

    resolved = await api_keys.resolve_key(db=db, raw_key=raw_key)
    assert resolved is not None
    assert resolved["id"] == row.id
    assert resolved["rate_limit"] == 42


async def test_resolve_unknown_key_returns_none(db):
    assert await api_keys.resolve_key(db=db, raw_key="sk-sent-doesnotexist") is None


async def test_revoked_key_no_longer_resolves(db):
    row, raw_key = await api_keys.create_key(db=db, name="to-revoke", rate_limit=100)
    await api_keys.revoke_key(db=db, key_id=row.id)

    assert await api_keys.resolve_key(db=db, raw_key=raw_key) is None


async def test_revoked_key_stays_revoked_even_from_cache(db):
    """A revoked key's negative result gets cached too — repeated use of a
    revoked key shouldn't keep round-tripping to Postgres."""
    row, raw_key = await api_keys.create_key(db=db, name="to-revoke", rate_limit=100)
    await api_keys.revoke_key(db=db, key_id=row.id)

    assert await api_keys.resolve_key(db=db, raw_key=raw_key) is None
    assert await api_keys.resolve_key(db=db, raw_key=raw_key) is None  # cached path


async def test_rotate_invalidates_the_old_key_and_issues_a_new_one(db):
    row, old_raw = await api_keys.create_key(db=db, name="rotate-me", rate_limit=100)
    result = await api_keys.rotate_key(db=db, key_id=row.id)
    assert result is not None
    new_row, new_raw = result

    assert new_row.id == row.id  # same logical key, new secret
    assert new_raw != old_raw
    assert await api_keys.resolve_key(db=db, raw_key=old_raw) is None
    assert (await api_keys.resolve_key(db=db, raw_key=new_raw))["id"] == row.id


async def test_rotate_unknown_key_returns_none(db):
    assert await api_keys.rotate_key(db=db, key_id="not-a-real-id") is None


async def test_list_keys_orders_newest_first(db):
    _row1, _ = await api_keys.create_key(db=db, name="first", rate_limit=100)
    row2, _ = await api_keys.create_key(db=db, name="second", rate_limit=100)

    rows = await api_keys.list_keys(db=db)
    assert rows[0].id == row2.id
