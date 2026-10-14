import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["EMBEDDING_MODEL"] = "hash"
os.environ["AUTO_ANALYZE_ON_CHANGE"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-0123456789"
os.environ["APP_ENV"] = "dev"
os.environ["UPLOADS_DIR"] = tempfile.mkdtemp()

import asyncio  # noqa: E402

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client():
    """App lifespan runs in one dedicated task: the MCP session manager's task group must be entered and
    exited in the same task, and pytest runs fixture setup and teardown in different ones."""
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold_lifespan():
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold_lifespan())
    await started.wait()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        stop.set()
        await task


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def admin_headers(client):
    r = await client.post("/api/auth/login", data={"username": "admin@example.com", "password": "admin1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def requester_headers(client):
    r = await client.post("/api/auth/login", data={"username": "karim.benali@example.com", "password": "demo1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
