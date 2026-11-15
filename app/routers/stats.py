from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Match, Player, Rating

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def get_stats(response: Response, db: AsyncSession = Depends(get_db)):
    player_count = (await db.execute(select(func.count(Player.id)))).scalar() or 0
    match_count = (await db.execute(select(func.count(Match.id)).where(Match.status == "confirmed"))).scalar() or 0

    # The site's headline ladder is 1v1; a 3v3 rating must not appear as the
    # "top player" on the landing page.
    top_rating = (await db.execute(
        select(Rating)
        .where(Rating.player_id.isnot(None), Rating.format == "1v1")
        .order_by(Rating.elo.desc())
        .limit(1)
    )).scalar_one_or_none()

    top_player = None
    if top_rating and top_rating.player_id:
        player = await db.get(Player, top_rating.player_id)
        top_player = {"id": str(player.id), "username": player.username} if player else None

    response.headers["Cache-Control"] = "public, max-age=15"
    return {
        "total_players": player_count,
        "total_matches": match_count,
        "top_player": top_player,
    }
