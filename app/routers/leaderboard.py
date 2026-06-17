from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Rating, Player, Team, TeamMember, Season, SeasonSnapshot
from app.schemas import LeaderboardEntry
from app.ranks import get_rank, TOP_RANK_MIN_ELO, RANKS

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])


@router.get("/{format}", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    format: str,
    limit: int = Query(50, ge=1, le=200),
    rank: str = Query(None, description="Filter by rank tier (Bronze, Silver, Gold, etc.)"),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import and_
    q = select(Rating).options(selectinload(Rating.player)).where(
        Rating.format == format, Rating.player_id.isnot(None)
    )

    if rank:
        tiers = {}
        for name, threshold, _ in RANKS:
            tier = name.split()[0]
            if tier not in tiers:
                tiers[tier] = [threshold, threshold]
            else:
                tiers[tier][1] = threshold
        if rank in tiers:
            min_elo = tiers[rank][0]
            max_elo = tiers[rank][1]
            q = q.where(and_(Rating.elo >= min_elo, Rating.elo <= max_elo))

    q = q.order_by(desc(Rating.elo)).limit(limit)
    result = await db.execute(q)
    ratings = result.scalars().all()

    entries = []
    for i, r in enumerate(ratings, 1):
        total = r.wins + r.losses
        win_rate = round(r.wins / total, 3) if total > 0 else 0.0
        rank_title, rank_color = get_rank(r.elo, i if i <= 10 and r.elo >= TOP_RANK_MIN_ELO else None)
        entries.append(LeaderboardEntry(
            rank=i,
            player_id=r.player_id,
            name=r.player.username if r.player else "Unknown",
            elo=r.elo,
            rank_title=rank_title,
            rank_color=rank_color,
            matches_played=r.matches_played,
            wins=r.wins,
            losses=r.losses,
            win_rate=win_rate,
            streak=r.streak or 0,
        ))
    return entries


@router.get("/seasons/list")
async def list_seasons_public(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Season).order_by(Season.start_date.desc())
    )
    seasons = result.scalars().all()
    return [{
        "id": str(s.id),
        "name": s.name,
        "status": s.status,
        "start_date": s.start_date.isoformat(),
        "end_date": s.end_date.isoformat() if s.end_date else None,
    } for s in seasons]


@router.get("/seasons/{season_id}")
async def get_season_public(season_id: str, db: AsyncSession = Depends(get_db)):
    import uuid
    sid = uuid.UUID(season_id)
    season = await db.get(Season, sid)
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")

    snapshots = (await db.execute(
        select(SeasonSnapshot)
        .where(SeasonSnapshot.season_id == season.id)
        .order_by(SeasonSnapshot.position)
    )).scalars().all()

    return {
        "id": str(season.id),
        "name": season.name,
        "status": season.status,
        "start_date": season.start_date.isoformat(),
        "end_date": season.end_date.isoformat() if season.end_date else None,
        "standings": [{
            "player_id": str(s.player_id),
            "player_name": s.player_name,
            "elo": s.elo,
            "rank_title": s.rank_title,
            "rank_color": s.rank_color,
            "wins": s.wins,
            "losses": s.losses,
            "position": s.position,
        } for s in snapshots],
    }


@router.get("/{format}/team", response_model=list[LeaderboardEntry])
async def get_team_leaderboard(
    format: str,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Rating)
        .where(Rating.format == format, Rating.team_id.isnot(None))
        .order_by(desc(Rating.elo))
        .limit(limit)
    )
    ratings = result.scalars().all()

    entries = []
    for i, r in enumerate(ratings, 1):
        team = r.team
        if team and team.name:
            name = team.name
        elif team:
            members_result = await db.execute(
                select(Player).join(TeamMember, TeamMember.player_id == Player.id)
                .where(TeamMember.team_id == team.id)
            )
            members = members_result.scalars().all()
            name = " + ".join(m.username for m in members)
        else:
            name = "Unknown Team"

        total = r.wins + r.losses
        win_rate = round(r.wins / total, 3) if total > 0 else 0.0

        rank_title, rank_color = get_rank(r.elo)
        entries.append(LeaderboardEntry(
            rank=i,
            team_id=r.team_id,
            name=name,
            elo=r.elo,
            rank_title=rank_title,
            rank_color=rank_color,
            matches_played=r.matches_played,
            wins=r.wins,
            losses=r.losses,
            win_rate=win_rate,
        ))
    return entries
