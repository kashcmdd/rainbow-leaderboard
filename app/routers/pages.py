from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.csrf import csrf_input
from app.database import get_db
from app.models import Player

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.globals["csrf_input"] = csrf_input
templates.env.globals["site_name"] = settings.site_name
templates.env.globals["public_base_url"] = settings.public_base_url
templates.env.globals["dev_login_enabled"] = bool(settings.dev_login_discord_id)
templates.env.globals["dev_login_username"] = settings.dev_login_username


async def _is_admin(request: Request, db: AsyncSession) -> bool:
    """Keep the page guard in sync with the API guard (DB + allowlist)."""
    user = request.session.get("user")
    if not user:
        return False
    is_admin = user["id"] in settings.admin_discord_ids
    if not is_admin:
        result = await db.execute(select(Player).where(Player.discord_id == user["id"]))
        player = result.scalar_one_or_none()
        is_admin = player is not None and player.is_admin
    if user.get("is_admin") != is_admin:
        user["is_admin"] = is_admin
        request.session["user"] = user
    return is_admin


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request, format: str = "1v1"):
    return templates.TemplateResponse(request, "leaderboard.html", {"format": format})


@router.get("/player/{player_id}", response_class=HTMLResponse)
async def player_page(request: Request, player_id: str):
    return templates.TemplateResponse(request, "player.html", {"player_id": player_id})


@router.get("/team/{team_id}", response_class=HTMLResponse)
async def team_page(request: Request, team_id: str):
    return templates.TemplateResponse(request, "team.html", {"team_id": team_id})


@router.get("/match/{match_id}", response_class=HTMLResponse)
async def match_page(request: Request, match_id: str):
    return templates.TemplateResponse(request, "match.html", {"match_id": match_id})


@router.get("/player/{player_id}/edit", response_class=HTMLResponse)
async def edit_player_page(request: Request, player_id: str, db: AsyncSession = Depends(get_db)):
    if not await _is_admin(request, db):
        return RedirectResponse(url=f"/player/{player_id}", status_code=302)
    return templates.TemplateResponse(request, "player_edit.html", {"player_id": player_id})


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: AsyncSession = Depends(get_db)):
    if not await _is_admin(request, db):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "admin.html")

@router.get("/player/new", response_class=HTMLResponse)
async def new_player_page(request: Request):
    return templates.TemplateResponse(request, "player_new.html")


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, db: AsyncSession = Depends(get_db)):
    session_user = request.session.get("user")
    if not session_user:
        return RedirectResponse(url="/auth/login", status_code=302)
    did = session_user["id"]
    result = await db.execute(
        select(Player).where(Player.discord_id == did)
    )
    player = result.scalar_one_or_none()
    if not player:
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=f"/player/{player.id}", status_code=302)

@router.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    return templates.TemplateResponse(request, "compare.html")


@router.get("/tournaments", response_class=HTMLResponse)
async def tournaments_list_page(request: Request):
    return templates.TemplateResponse(request, "tournaments_list.html")

@router.get("/tournament/{tournament_id}", response_class=HTMLResponse)
async def tournament_page(request: Request, tournament_id: str):
    from app.config import settings
    return templates.TemplateResponse(request, "tournament.html", {"tournament_id": tournament_id, "admin_discord_ids": settings.admin_discord_ids})

@router.get("/seasons", response_class=HTMLResponse)
async def seasons_page(request: Request):
    return templates.TemplateResponse(request, "seasons.html")


@router.get("/ratings", response_class=HTMLResponse)
async def ratings_page(request: Request):
    return templates.TemplateResponse(request, "ratings.html")

@router.get("/report", response_class=HTMLResponse)
async def report_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    did = user["id"]
    result = await db.execute(select(Player).where(Player.discord_id == did))
    player = result.scalar_one_or_none()
    if player and player.is_banned:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "report_match.html")
