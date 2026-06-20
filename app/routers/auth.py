import secrets
import httpx
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from urllib.parse import urlencode

from app.config import settings
from app.database import get_db
from app.models import Player, Rating
from app.csrf import csrf_input

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.globals["csrf_input"] = csrf_input

DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_BASE = "https://discord.com/api/v10"


def _discord_avatar_url(user_id: str, avatar_hash: str) -> str:
    ext = "gif" if avatar_hash.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}"


@router.get("/login")
async def login(request: Request):
    user = request.session.get("user")
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/discord")
async def discord_login(request: Request):
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return RedirectResponse(url=f"{DISCORD_AUTH_URL}?{urlencode(params)}", status_code=302)


@router.get("/callback")
async def callback(request: Request, code: str, state: str = None, db: AsyncSession = Depends(get_db)):
    expected = request.session.pop("oauth_state", None)
    if not expected or not state or state != expected:
        return RedirectResponse(url="/auth/login?error=failed", status_code=302)
    data = {
        "client_id": settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.discord_redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(DISCORD_TOKEN_URL, data=data, headers=headers)
        if resp.status_code != 200:
            return RedirectResponse(url="/auth/login?error=failed", status_code=302)
        token_data = resp.json()
        access_token = token_data["access_token"]

        user_resp = await client.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            return RedirectResponse(url="/auth/login?error=failed", status_code=302)
        discord_user = user_resp.json()

    discord_id = discord_user["id"]
    username = discord_user.get("global_name") or discord_user["username"]
    avatar_hash = discord_user.get("avatar", "")
    avatar_url = _discord_avatar_url(discord_id, avatar_hash) if avatar_hash else None

    result = await db.execute(select(Player).where(Player.discord_id == discord_id))
    player = result.scalar_one_or_none()

    if not player:
        player = Player(username=username, discord_id=discord_id, avatar_url=avatar_url)
        db.add(player)
        await db.flush()
        rating = Rating(player_id=player.id, format="1v1", elo=0, wins=0, losses=0, matches_played=0)
        db.add(rating)
        await db.commit()
    else:
        needs_update = False
        if player.username != username:
            player.username = username
            needs_update = True
        if player.avatar_url != avatar_url:
            player.avatar_url = avatar_url
            needs_update = True
        if needs_update:
            await db.commit()

    if player and player.is_banned:
        return RedirectResponse(url="/auth/login?error=banned", status_code=302)

    is_admin = discord_id in settings.admin_discord_ids or (player and player.is_admin)

    request.session["user"] = {
        "id": discord_id,
        "username": username,
        "avatar": avatar_hash,
        "is_admin": is_admin,
    }

    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)
