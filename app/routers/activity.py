from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone

from app.database import get_db
from app.models import Match, Player, Team, TeamMember

router = APIRouter(prefix="/api/activity", tags=["activity"])


def _time_ago(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    diff = now - dt
    mins = int(diff.total_seconds() / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%b %d")


@router.get("")
async def get_activity(limit: int = Query(20, ge=1, le=50), db: AsyncSession = Depends(get_db)):
    items = []

    # Recent matches
    matches_result = await db.execute(
        select(Match)
        .options(
            selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
            selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
        )
        .order_by(desc(Match.played_at))
        .limit(limit)
    )
    matches = matches_result.scalars().all()

    for m in matches:
        def team_name(team):
            if team and team.name:
                return team.name
            if team:
                return " + ".join(mem.player.username for mem in team.members)
            return "Unknown"

        a_name = team_name(m.team_a)
        b_name = team_name(m.team_b)
        if not m.winner_team_id:
            continue
        winner = a_name if m.team_a and m.winner_team_id == m.team_a_id else b_name
        score = f"{m.score_a}-{m.score_b}"
        items.append({
            "type": "match",
            "message": f"{winner} won {score} vs {b_name if winner == a_name else a_name}",
            "detail": m.format.upper(),
            "timestamp": m.played_at.isoformat(),
            "time_ago": _time_ago(m.played_at),
        })

    # New player signups
    players_result = await db.execute(
        select(Player)
        .order_by(desc(Player.created_at))
        .limit(limit)
    )
    players = players_result.scalars().all()

    for p in players:
        items.append({
            "type": "player_join",
            "message": f"{p.username} joined the leaderboard",
            "detail": None,
            "timestamp": p.created_at.isoformat(),
            "time_ago": _time_ago(p.created_at),
        })

    # Sort combined by timestamp descending, take top `limit`
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    return items[:limit]
