import re
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.csrf import csrf_input
from app.database import get_db
from app.models import Player, Rating
from app.ratelimit import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.globals["csrf_input"] = csrf_input
templates.env.globals["site_name"] = settings.site_name
templates.env.globals["public_base_url"] = settings.public_base_url
templates.env.globals["dev_login_enabled"] = bool(settings.dev_login_discord_id)
templates.env.globals["dev_login_username"] = settings.dev_login_username

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
    return templates.TemplateResponse(request, "login.html")


@router.get("/discord")
async def discord_login(request: Request, _rl: None = Depends(rate_limit(20, 60))):
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


@router.get("/dev")
async def dev_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit(20, 60)),
):
    """Testing bypass. Disabled unless DEV_LOGIN_DISCORD_ID is set."""
    if not settings.dev_login_discord_id:
        raise HTTPException(status_code=404, detail="Not found")

    discord_id = settings.dev_login_discord_id
    player = (await db.execute(
        select(Player).where(Player.discord_id == discord_id)
    )).scalar_one_or_none()

    if not player:
        base = (settings.dev_login_username or f"dev-{discord_id[-4:]}").strip()[:64]
        name = base
        n = 1
        while (await db.execute(select(Player).where(Player.username == name))).scalar_one_or_none():
            n += 1
            suffix = f"-{n}"
            name = base[: 64 - len(suffix)] + suffix
        player = Player(username=name, discord_id=discord_id, is_admin=settings.dev_login_admin)
        db.add(player)
        await db.flush()
        db.add(Rating(player_id=player.id, format="1v1", elo=settings.starting_elo,
                       rd=settings.starting_rd, volatility=settings.starting_volatility,
                       wins=0, losses=0, matches_played=0))
        await db.commit()
    elif settings.dev_login_admin and not player.is_admin:
        player.is_admin = True
        await db.commit()

    is_admin = (
        settings.dev_login_admin
        or discord_id in settings.admin_discord_ids
        or player.is_admin
    )
    request.session["user"] = {
        "id": discord_id,
        "username": player.username,
        "avatar": None,
        "is_admin": is_admin,
    }
    return RedirectResponse(url="/", status_code=302)


@router.get("/callback")
async def callback(request: Request, code: str, state: str = None, db: AsyncSession = Depends(get_db), _rl: None = Depends(rate_limit(30, 60))):
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
    raw_name = discord_user.get("global_name") or discord_user.get("username") or ""
    # Player usernames are unique and limited to 64 chars; Discord names can
    # collide or contain characters we do not want in a URL/table cell.
    base = re.sub(r"[^\w\-. ]+", "", str(raw_name), flags=re.UNICODE).strip()[:64].strip()
    if not base:
        base = f"player-{discord_id[-4:]}"

    avatar_hash = discord_user.get("avatar", "")
    avatar_url = _discord_avatar_url(discord_id, avatar_hash) if avatar_hash else None

    result = await db.execute(select(Player).where(Player.discord_id == discord_id))
    player = result.scalar_one_or_none()

    if player and player.is_banned:
        return RedirectResponse(url="/auth/login?error=banned", status_code=302)

    async def _unique_username(candidate: str, exclude_id=None) -> str:
        name = candidate
        n = 1
        while True:
            existing = (await db.execute(
                select(Player).where(Player.username == name)
            )).scalar_one_or_none()
            if existing is None or (exclude_id is not None and existing.id == exclude_id):
                return name
            n += 1
            suffix = f"-{n}"
            name = candidate[: 64 - len(suffix)] + suffix

    if not player:
        username = await _unique_username(base)
        player = Player(username=username, discord_id=discord_id, avatar_url=avatar_url)
        db.add(player)
        try:
            await db.flush()
            rating = Rating(player_id=player.id, format="1v1", elo=settings.starting_elo,
                       rd=settings.starting_rd, volatility=settings.starting_volatility,
                       wins=0, losses=0, matches_played=0)
            db.add(rating)
            await db.commit()
        except IntegrityError:
            # Another login won the race between the uniqueness check and insert.
            await db.rollback()
            player = (await db.execute(
                select(Player).where(Player.discord_id == discord_id)
            )).scalar_one_or_none()
            if player is None:
                suffix = "-" + uuid.uuid4().hex[:4]
                player = Player(username=base[: 64 - len(suffix)] + suffix,
                                discord_id=discord_id, avatar_url=avatar_url)
                db.add(player)
                await db.flush()
                db.add(Rating(player_id=player.id, format="1v1", elo=settings.starting_elo,
                       rd=settings.starting_rd, volatility=settings.starting_volatility,
                       wins=0, losses=0, matches_played=0))
                await db.commit()
    else:
        needs_update = False
        desired = await _unique_username(base, exclude_id=player.id)
        if player.username != desired:
            player.username = desired
            needs_update = True
        if player.avatar_url != avatar_url:
            player.avatar_url = avatar_url
            needs_update = True
        if needs_update:
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()

    is_admin = discord_id in settings.admin_discord_ids or (player and player.is_admin)

    request.session["user"] = {
        "id": discord_id,
        "username": username if not player else player.username,
        "avatar": avatar_hash,
        "is_admin": is_admin,
    }

    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)
