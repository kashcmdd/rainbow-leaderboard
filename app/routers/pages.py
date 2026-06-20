from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from app.database import get_db
from app.deps import require_user
from app.models import Player, Player as PlayerModel
from app.csrf import csrf_input

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.globals["csrf_input"] = csrf_input


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request, format: str = "1v1"):
    return templates.TemplateResponse("leaderboard.html", {"request": request, "format": format})


@router.get("/player/{player_id}", response_class=HTMLResponse)
async def player_page(request: Request, player_id: str):
    return templates.TemplateResponse("player.html", {"request": request, "player_id": player_id})


@router.get("/player/{player_id}/edit", response_class=HTMLResponse)
async def edit_player_page(request: Request, player_id: str):
    user = request.session.get("user")
    if not user or not user.get("is_admin"):
        return RedirectResponse(url=f"/player/{player_id}", status_code=302)
    return templates.TemplateResponse("player_edit.html", {"request": request, "player_id": player_id})


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = request.session.get("user")
    if not user or not user.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request})

@router.get("/player/new", response_class=HTMLResponse)
async def new_player_page(request: Request):
    return templates.TemplateResponse("player_new.html", {"request": request})


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
    return templates.TemplateResponse("compare.html", {"request": request})


@router.get("/tournaments", response_class=HTMLResponse)
async def tournaments_list_page(request: Request):
    return templates.TemplateResponse("tournaments_list.html", {"request": request})

@router.get("/tournament/{tournament_id}", response_class=HTMLResponse)
async def tournament_page(request: Request, tournament_id: str):
    from app.config import settings
    return templates.TemplateResponse("tournament.html", {"request": request, "tournament_id": tournament_id, "admin_discord_ids": settings.admin_discord_ids})

@router.get("/seasons", response_class=HTMLResponse)
async def seasons_page(request: Request):
    return templates.TemplateResponse("seasons.html", {"request": request})

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
    return templates.TemplateResponse("report_match.html", {"request": request})



