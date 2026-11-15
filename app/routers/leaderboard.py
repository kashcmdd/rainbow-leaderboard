import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import Player, Rating, RatingHistory, Season, SeasonSnapshot, Team, TeamMember
from app.ranks import RANKS, TOP_RANK_MIN_ELO, get_rank
from app.schemas import LeaderboardEntry

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

async def _recent_signals(db: AsyncSession, rating_ids: list) -> dict:
    """Form (last five) and 7-day movement for a set of rating ids."""
    history_map = {}
    if not rating_ids:
        return history_map
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    rows = (await db.execute(
        select(RatingHistory.rating_id, RatingHistory.elo_before,
               RatingHistory.elo_after, RatingHistory.changed_at)
        .where(
            RatingHistory.rating_id.in_(rating_ids),
            RatingHistory.changed_at >= cutoff,
        )
        .order_by(desc(RatingHistory.changed_at))
    )).all()
    for rating_id, before, after, _changed in rows:
        delta = after - before
        bucket = history_map.setdefault(rating_id, {"form": [], "movement": 0})
        if delta != 0 and len(bucket["form"]) < 5:
            bucket["form"].append("W" if delta > 0 else "L")
        bucket["movement"] += delta
    return history_map



@router.get("/{format}", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    format: str,
    limit: int = Query(50, ge=1, le=200),
    rank: str = Query(None, description="Filter by rank tier (Bronze, Silver, Gold, etc.)"),
    season: str = Query(None, description="Archived season id to read instead of live ratings"),
    db: AsyncSession = Depends(get_db),
):

    # Historical ladder for an archived season (1v1 snapshots).
    if season:
        import uuid as _uuid
        try:
            sid = _uuid.UUID(season)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid season id") from None
        season_obj = await db.get(Season, sid)
        if season_obj and season_obj.status == "archived":
            snapshot_rows = (await db.execute(
                select(SeasonSnapshot)
                .where(SeasonSnapshot.season_id == sid)
                .order_by(SeasonSnapshot.position)
            )).scalars().all()
            if rank:
                snapshot_rows = [
                    s for s in snapshot_rows
                    if s.rank_title.lower().startswith(rank.lower())
                ]
            avatar_map = {}
            player_ids = [s.player_id for s in snapshot_rows if s.player_id]
            if player_ids:
                rows = (await db.execute(
                    select(Player.id, Player.avatar_url).where(Player.id.in_(player_ids))
                )).all()
                avatar_map = {pid: url for pid, url in rows}
            entries = []
            for i, s in enumerate(snapshot_rows, 1):
                total = (s.wins or 0) + (s.losses or 0)
                entries.append(LeaderboardEntry(
                    rank=s.position or i,
                    player_id=s.player_id,
                    name=s.player_name,
                    elo=s.elo,
                    rank_title=s.rank_title,
                    rank_color=s.rank_color,
                    matches_played=total,
                    wins=s.wins or 0,
                    losses=s.losses or 0,
                    win_rate=round((s.wins or 0) / total, 3) if total > 0 else 0.0,
                    streak=0,
                    avatar_url=avatar_map.get(s.player_id),
                    form=[],
                    movement_7d=0,
                    rd=0.0,
                    provisional=False,
                ))
            return entries

    q = select(Rating).options(selectinload(Rating.player)).where(
        Rating.format == format, Rating.player_id.isnot(None)
    )

    if rank:
        # Build tier ranges from rank thresholds
        tiers = {}
        tier_order = []
        for name, threshold, _ in RANKS:
            tier = name.split()[0]
            if tier not in tiers:
                tiers[tier] = {"min": threshold, "max": None}
                tier_order.append(tier)
            else:
                tiers[tier]["max"] = threshold
        # Set upper bounds exclusive — last tier has no upper bound
        for tier in tier_order:
            if tiers[tier]["max"] is not None:
                tiers[tier]["max"] = tiers[tier]["max"] - 1
            else:
                # Highest rank — no cap
                tiers[tier]["max"] = None
        if rank in tiers:
            min_elo = tiers[rank]["min"]
            max_elo = tiers[rank]["max"]
            q = q.where(Rating.elo >= min_elo)
            if max_elo is not None:
                q = q.where(Rating.elo <= max_elo)

    q = q.order_by(desc(Rating.elo)).limit(limit)
    result = await db.execute(q)
    ratings = result.scalars().all()

    history_map = await _recent_signals(db, [r.id for r in ratings])

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
            avatar_url=r.player.avatar_url if r.player else None,
            form=history_map.get(r.id, {}).get("form", []),
            movement_7d=history_map.get(r.id, {}).get("movement", 0),
            rd=round(r.rd or 0, 1),
            provisional=(r.rd or 0) > settings.provisional_rd,
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
    rating_q = (
        select(Rating)
        .options(selectinload(Rating.team).selectinload(Team.members).selectinload(TeamMember.player))
        .where(Rating.format == format, Rating.team_id.isnot(None))
        .order_by(desc(Rating.elo))
        .limit(limit)
    )
    result = await db.execute(rating_q)
    ratings = result.scalars().all()
    history_map = await _recent_signals(db, [r.id for r in ratings])

    entries = []
    for i, r in enumerate(ratings, 1):
        team = r.team
        if team and team.name:
            name = team.name
        elif team:
            name = " + ".join(m.player.username for m in team.members) if team.members else "Unknown Team"
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
            avatar_url=None,
            form=history_map.get(r.id, {}).get("form", []),
            movement_7d=history_map.get(r.id, {}).get("movement", 0),
            rd=round(r.rd or 0, 1),
            provisional=(r.rd or 0) > settings.provisional_rd,
        ))
    return entries


@router.get("/{format}/export")
async def export_leaderboard(
    format: str,
    type: str = Query("csv"),
    db: AsyncSession = Depends(get_db),
):
    q = select(Rating).options(selectinload(Rating.player)).where(
        Rating.format == format, Rating.player_id.isnot(None)
    ).order_by(desc(Rating.elo))

    result = await db.execute(q)
    ratings = result.scalars().all()

    export_data = []
    for i, r in enumerate(ratings, 1):
        total = r.wins + r.losses
        win_rate = round(r.wins / total, 3) if total > 0 else 0.0
        rank_title, _ = get_rank(r.elo, i if i <= 10 else None)
        export_data.append({
            "rank": i,
            "name": r.player.username if r.player else "Unknown",
            "elo": r.elo,
            "rank_title": rank_title,
            "wins": r.wins,
            "losses": r.losses,
            "win_rate": win_rate,
            "streak": r.streak or 0,
        })

    if type == "json":
        return Response(
            content=json.dumps(export_data),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="leaderboard_{format}.json"'},
        )

    csv_lines = ["rank,name,elo,rank_title,wins,losses,win_rate,streak"]
    for row in export_data:
        csv_lines.append(f'{row["rank"]},{row["name"]},{row["elo"]},{row["rank_title"]},{row["wins"]},{row["losses"]},{row["win_rate"]},{row["streak"]}')

    return Response(
        content="\n".join(csv_lines),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="leaderboard_{format}.csv"'},
    )
