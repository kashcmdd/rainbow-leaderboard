import os
import pytest_asyncio
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env.test"
load_dotenv(env_path, override=True)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://rainbow:g4F6McABJjaPBwYDMSTZXwHt05k@db:5432/rainbow_test")

from app.database import init_db, engine
from app.models import Base
from app.config import settings

settings.database_url = os.environ["DATABASE_URL"]

from httpx import ASGITransport, AsyncClient
from starlette.middleware.sessions import b64encode
from itsdangerous import TimestampSigner
import json


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
