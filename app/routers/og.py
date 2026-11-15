import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Match, Team, TeamMember

router = APIRouter(prefix="/og", tags=["og"])

WIDTH, HEIGHT = 1200, 630
BG = "#0b0d0e"
GRID = "#151a1d"
INK = "#e9edee"
INK2 = "#a3adb1"
INK3 = "#7b868b"
SIGNAL = "#e8197d"
WIN = "#59d38c"
LOSS = "#e86767"

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _team_name(team: Team) -> str:
    if team.name and team.name.upper() != "TBD":
        return team.name
    names = [m.player.username for m in team.members if m.player]
    return " + ".join(names) if names else "TBD"


@router.get("/match/{match_id}.png")
async def match_card(match_id: str, db: AsyncSession = Depends(get_db)):
    try:
        mid = uuid.UUID(match_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Match not found") from None
    match = (await db.execute(
        select(Match)
        .options(
            selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
            selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
        )
        .where(Match.id == mid)
    )).scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    for x in range(0, WIDTH, 48):
        draw.line([(x, 0), (x, HEIGHT)], fill=GRID, width=1)
    for y in range(0, HEIGHT, 48):
        draw.line([(0, y), (WIDTH, y)], fill=GRID, width=1)
    draw.rectangle([0, 0, WIDTH, 6], fill=SIGNAL)

    bold = _font("DejaVuSans-Bold.ttf", 44)
    name_font = _font("DejaVuSans-Bold.ttf", 58)
    score_font = _font("DejaVuSans-Bold.ttf", 116)
    mono = _font("DejaVuSansMono.ttf", 24)
    small = _font("DejaVuSansMono.ttf", 20)

    draw.text((72, 56), "VANTAGE", font=bold, fill=INK)
    played = match.played_at.astimezone().strftime("%d %b %Y") if isinstance(match.played_at, datetime) else ""
    draw.text((72, 118), f"{match.format.upper()}  ·  {played}".strip(" ·"), font=mono, fill=INK3)

    a_name = _team_name(match.team_a)
    b_name = _team_name(match.team_b)
    decided = match.status == "confirmed" and match.winner_team_id is not None
    a_won = match.winner_team_id == match.team_a_id
    a_color = WIN if decided and a_won else INK if decided else INK2
    b_color = WIN if decided and not a_won else INK if decided else INK2

    draw.text((72, 250), _fit(draw, a_name, name_font, 760), font=name_font, fill=a_color)
    draw.text((72, 330), _fit(draw, b_name, name_font, 760), font=name_font, fill=b_color)

    score = f"{match.score_a} : {match.score_b}"
    draw.text((WIDTH - 72, 250), score, font=score_font, fill=INK, anchor="ra")

    if decided:
        draw.text((WIDTH - 72, 390), "WIN" if a_won else "LOSS", font=mono, fill=WIN, anchor="ra")
        swing = abs(match.elo_delta_a or 0) + abs(match.elo_delta_b or 0)
        draw.text((WIDTH - 72, 424), f"{swing} ELO swing", font=small, fill=INK3, anchor="ra")
    else:
        draw.text((WIDTH - 72, 390), match.status.upper(), font=mono, fill=SIGNAL, anchor="ra")

    details = []
    if match.map_name:
        details.append(match.map_name)
    if match.side_a:
        details.append(f"{match.side_a} side")
    if match.operator_bans:
        details.append("Bans")
    if details:
        draw.text((72, 444), "  ·  ".join(details), font=mono, fill=INK2)

    draw.text((72, HEIGHT - 64), f"Match {str(match.id)[:8]}", font=small, fill=INK3)
    draw.text((WIDTH - 72, HEIGHT - 64), "Vantage ladder", font=small, fill=INK3, anchor="ra")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return Response(
        content=out.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )
