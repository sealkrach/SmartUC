import pytest
from sqlalchemy import text

from app.db import _sync_additive_columns, engine

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_missing_columns_are_added(client):
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE users DROP COLUMN is_demo_guest"))
        added = await conn.run_sync(_sync_additive_columns)
        assert "users.is_demo_guest" in added
        cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(users)"))).all()]
        assert "is_demo_guest" in cols
        assert await conn.run_sync(_sync_additive_columns) == []
