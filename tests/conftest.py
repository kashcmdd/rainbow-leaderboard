import os
from pathlib import Path

import pytest_asyncio
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / ".env.test"
# Do not override a DATABASE_URL already present in the environment: CI points
# at its own postgres service, while a local run falls back to .env.test.
load_dotenv(env_path, override=False)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://rainbow:g4F6McABJjaPBwYDMSTZXwHt05k@localhost:5432/rainbow_test")
# Tests run over http://test; Secure cookies would never be sent back.
os.environ.setdefault("COOKIE_SECURE", "false")
# CI has no .env; exercise the request-URL fallback for absolute meta links.
os.environ.setdefault("PUBLIC_BASE_URL", "")

from app.config import settings
from app.database import engine, init_db
from app.models import Base

settings.database_url = os.environ["DATABASE_URL"]

import json

from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from starlette.middleware.sessions import b64encode


@pytest_asyncio.fixture(autouse=True)
def _clear_rate_limits():
    """The in-process limiter is global; keep tests independent."""
    from app.ratelimit import _buckets
    _buckets.clear()
    yield
    _buckets.clear()


MOCK_SESSION = {
    "user": {
        "id": "1415420243836407878",
        "username": "TestAdmin",
        "avatar": None,
        "is_admin": True,
    }
}


def _make_session_cookie(secret_key, session_data=MOCK_SESSION) -> str:
    signer = TimestampSigner(str(secret_key))
    raw = json.dumps(session_data, separators=(",", ":")).encode("utf-8")
    data = b64encode(raw)
    signed = signer.sign(data)
    return signed.decode("utf-8")


@pytest_asyncio.fixture
async def db_setup():
    await init_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_setup):
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        cookie = _make_session_cookie(settings.secret_key)
        ac.cookies.set("session", cookie)
        yield ac


@pytest_asyncio.fixture
async def anon_client(db_setup):
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def csrf_token(client):
    r = await client.get("/api/csrf/token")
    return r.json()["csrf_token"]


@pytest_asyncio.fixture
async def headers(client, csrf_token):
    return {
        "X-CSRF-Token": csrf_token,
        "Content-Type": "application/json",
    }


@pytest_asyncio.fixture
async def admin_headers(headers):
    return headers
