from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditLog, Player, Rating, Season, SeasonSnapshot
from app.ranks import get_rank


async def close_season(
    db: AsyncSession,
    name: str,
    actor_name: str = "Scheduler",
    actor_id: str = "system",
) -> dict:
    """Snapshot the active 1v1 season, archive it, and start the next one."""
    result = await db.execute(select(Season).where(Season.status == "active").limit(1))
    current = result.scalar_one_or_none()
    if not current:
        current = Season(name=name, status="active")
        db.add(current)
        await db.flush()

    ratings = (await db.execute(
        select(Rating)
        .where(Rating.format == "1v1", Rating.player_id.isnot(None))
        .order_by(Rating.elo.desc())
    )).scalars().all()

    for pos, rating in enumerate(ratings, 1):
        player = await db.get(Player, rating.player_id)
        rank_title, rank_color = get_rank(rating.elo, rating.top_position)
        db.add(SeasonSnapshot(
            season_id=current.id,
            player_id=rating.player_id,
            player_name=player.username if player else "Unknown",
            elo=rating.elo,
            rank_title=rank_title,
            rank_color=rank_color,
            wins=rating.wins,
            losses=rating.losses,
            position=pos,
        ))

    current.status = "archived"
    current.end_date = datetime.now(timezone.utc)

    existing_seasons = await db.execute(select(Season))
    season_count = len(existing_seasons.scalars().all())
    new_name = f"{name} {season_count + 1}" if name != "Season" else f"Season {season_count + 1}"
    new_season = Season(
        name=new_name,
        status="active",
        end_date=datetime.now(timezone.utc) + timedelta(days=settings.season_length_days),
    )
    db.add(new_season)

    for rating in ratings:
        rating.elo = settings.starting_elo
        rating.wins = 0
        rating.losses = 0
        rating.matches_played = 0
        rating.top_position = None
        rating.rd = settings.starting_rd
        rating.volatility = settings.starting_volatility
        rating.rd_updated_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        admin_id=actor_id,
        admin_name=actor_name,
        action="close_season",
        target_name=name,
    ))
    await db.commit()
    return {
        "status": "closed",
        "archived_season": str(current.id),
        "new_season": str(new_season.id),
    }
