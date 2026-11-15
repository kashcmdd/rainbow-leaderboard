import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Match, Rating, RatingHistory, Team, TeamMember
from app.ranks import get_rank

router = APIRouter(prefix="/api/teams", tags=["teams"])


def _team_name(team: Team) -> str:
    if team.name:
        return team.name
    names = [m.player.username for m in team.members if m.player]
    return " + ".join(names) if names else "Unknown Team"


@router.get("/{team_id}")
async def get_team(team_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    team = (await db.execute(
        select(Team)
        .options(selectinload(Team.members).selectinload(TeamMember.player))
        .where(Team.id == team_id)
    )).scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    rating = (await db.execute(
        select(Rating).where(Rating.team_id == team.id, Rating.format == team.format)
    )).scalar_one_or_none()

    member_ids = [m.player_id for m in team.members]
    member_ratings = {}
    if member_ids:
        rows = (await db.execute(
            select(Rating).where(
                Rating.player_id.in_(member_ids), Rating.format == team.format
            )
        )).scalars().all()
        member_ratings = {str(r.player_id): r for r in rows}

    members = []
    for m in team.members:
        if not m.player:
            continue
        r = member_ratings.get(str(m.player_id))
        rank_title, rank_color = get_rank(r.elo if r else 0)
        members.append({
            "id": str(m.player.id),
            "username": m.player.username,
            "avatar_url": m.player.avatar_url,
            "elo": r.elo if r else 0,
            "rank_title": rank_title,
            "rank_color": rank_color,
        })

    matches = (await db.execute(
        select(Match)
        .options(
            selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
            selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
        )
        .where(or_(Match.team_a_id == team.id, Match.team_b_id == team.id))
        .order_by(desc(Match.played_at))
        .limit(25)
    )).scalars().all()

    recent = []
    for m in matches:
        is_a = m.team_a_id == team.id
        opponent = m.team_b if is_a else m.team_a
        won = m.winner_team_id == team.id
        recent.append({
            "id": str(m.id),
            "opponent": _team_name(opponent),
            "result": "win" if won else "loss",
            "score_a": m.score_a,
            "score_b": m.score_b,
            "elo_delta": (m.elo_delta_a if is_a else m.elo_delta_b) or 0,
            "format": m.format,
            "played_at": m.played_at.isoformat(),
        })

    history_rows = []
    form = []
    movement = 0
    if rating:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        history_rows = (await db.execute(
            select(RatingHistory)
            .where(RatingHistory.rating_id == rating.id)
            .order_by(RatingHistory.changed_at.asc())
            .limit(60)
        )).scalars().all()
        for h in reversed(history_rows):
            if h.changed_at < cutoff:
                continue
            delta = h.elo_after - h.elo_before
            if delta != 0 and len(form) < 5:
                form.append("W" if delta > 0 else "L")
            movement += delta

    rank_title, rank_color = get_rank(rating.elo if rating else 0)
    return {
        "id": str(team.id),
        "name": _team_name(team),
        "format": team.format,
        "elo": rating.elo if rating else 0,
        "rank_title": rank_title,
        "rank_color": rank_color,
        "wins": rating.wins if rating else 0,
        "losses": rating.losses if rating else 0,
        "matches_played": rating.matches_played if rating else 0,
        "streak": (rating.streak or 0) if rating else 0,
        "form": form,
        "movement_7d": movement,
        "history": [
            {"elo": h.elo_after, "timestamp": h.changed_at.isoformat()}
            for h in history_rows
        ],
        "members": members,
        "recent_matches": recent,
    }
