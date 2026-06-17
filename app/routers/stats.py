from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Player, Match, Rating

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def get_stats(db: AsyncSession = Depends(get_db)):
    player_count = (await db.execute(select(func.count(Player.id)))).scalar() or 0
    match_count = (await db.execute(select(func.count(Match.id)))).scalar() or 0

    top_rating = (await db.execute(
        select(Rating).where(Rating.player_id.isnot(None)).order_by(Rating.elo.desc()).limit(1)
    )).scalar_one_or_none()

    top_player = None
    if top_rating and top_rating.player_id:
        player = await db.get(Player, top_rating.player_id)
        top_player = {"id": str(player.id), "username": player.username} if player else None

    return {
        "total_players": player_count,
        "total_matches": match_count,
        "top_player": top_player,
    }
