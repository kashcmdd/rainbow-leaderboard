import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.csrf import csrf_protect
from app.database import get_db
from app.deps import require_user
from app.models import Match, Player, Rating, RatingHistory, Team, TeamMember
from app.ranks import get_rank
from app.schemas import TeamCreate, TeamUpdate

router = APIRouter(prefix="/api/teams", tags=["teams"])


async def _viewer(db: AsyncSession, request: Request):
    user = request.session.get("user")
    if not user:
        return None, None, False
    player = (await db.execute(select(Player).where(Player.discord_id == user["id"]))).scalar_one_or_none()
    is_admin = bool(player and player.is_admin) or bool(user.get("is_admin")) or user["id"] in settings.admin_discord_ids
    return user, player, is_admin


def _can_manage(team: Team, player: Player | None, is_admin: bool) -> bool:
    return is_admin or bool(player and team.captain_id == player.id)


def _validate_roster(format: str, player_ids: list[str]) -> None:
    try:
        expected = int(format.split("v")[0])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid format") from None
    if len(player_ids) != expected:
        raise HTTPException(status_code=400, detail=f"{format} requires exactly {expected} player(s)")
    if len(set(player_ids)) != expected:
        raise HTTPException(status_code=400, detail="A player cannot appear twice")


@router.get("")
async def list_teams(db: AsyncSession = Depends(get_db)):
    teams = (await db.execute(
        select(Team)
        .options(selectinload(Team.members).selectinload(TeamMember.player))
        .where(Team.is_active.is_(True))
        .order_by(Team.name.asc().nulls_last(), Team.created_at.asc())
    )).scalars().all()
    ratings = (await db.execute(select(Rating).where(Rating.team_id.isnot(None)))).scalars().all()
    rating_map = {str(r.team_id): r for r in ratings}
    out = []
    for team in teams:
        rating = rating_map.get(str(team.id))
        rank_title, rank_color = get_rank(rating.elo if rating else 0)
        captain = next((m.player.username for m in team.members if m.player and m.player_id == team.captain_id), None)
        out.append({
            "id": str(team.id),
            "name": _team_name(team),
            "format": team.format,
            "elo": rating.elo if rating else 0,
            "rank_title": rank_title,
            "rank_color": rank_color,
            "matches_played": rating.matches_played if rating else 0,
            "wins": rating.wins if rating else 0,
            "losses": rating.losses if rating else 0,
            "captain": captain,
            "captain_id": str(team.captain_id) if team.captain_id else None,
            "managed": bool(team.name and team.name.upper() != "TBD"),
            "members": [{"id": str(m.player_id), "username": m.player.username} for m in team.members if m.player],
        })
    return out


@router.post("")
async def create_team(
    body: TeamCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_user),
    __: None = Depends(csrf_protect),
):
    user, creator, _is_admin = await _viewer(db, request)
    ids = [str(pid) for pid in body.player_ids]
    _validate_roster(body.format, ids)
    players = (await db.execute(select(Player).where(Player.id.in_(body.player_ids)))).scalars().all()
    if len(players) != len(ids):
        raise HTTPException(status_code=404, detail="One or more players were not found")

    existing = (await db.execute(
        select(Team)
        .options(selectinload(Team.members))
        .where(Team.format == body.format, Team.is_active.is_(True))
    )).scalars().all()
    captain_id = creator.id if creator and str(creator.id) in ids else players[0].id
    name = (body.name or " + ".join(p.username for p in players)).strip()[:128]
    for team in existing:
        if {str(m.player_id) for m in team.members} == set(ids):
            if team.name and team.name.upper() != "TBD":
                raise HTTPException(status_code=409, detail="A team with this roster already exists")
            # Adopt the anonymous shell created by earlier match reports.
            team.name = name
            team.captain_id = captain_id
            team.created_by = user["id"]
            rating = (await db.execute(
                select(Rating).where(Rating.team_id == team.id, Rating.format == body.format)
            )).scalar_one_or_none()
            if not rating:
                db.add(Rating(
                    team_id=team.id, format=body.format, elo=settings.starting_elo,
                    rd=settings.starting_rd, volatility=settings.starting_volatility,
                ))
            await db.commit()
            return {"id": str(team.id), "name": team.name, "adopted": True}

    team = Team(format=body.format, name=name, captain_id=captain_id, created_by=user["id"])
    db.add(team)
    await db.flush()
    for pid in body.player_ids:
        db.add(TeamMember(team_id=team.id, player_id=pid))
    db.add(Rating(
        team_id=team.id, format=body.format, elo=settings.starting_elo,
        rd=settings.starting_rd, volatility=settings.starting_volatility,
    ))
    await db.commit()
    return {"id": str(team.id), "name": team.name}


@router.patch("/{team_id}")
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_user),
    __: None = Depends(csrf_protect),
):
    team = (await db.execute(
        select(Team).options(selectinload(Team.members)).where(Team.id == team_id)
    )).scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _user, player, is_admin = await _viewer(db, request)
    if not _can_manage(team, player, is_admin):
        raise HTTPException(status_code=403, detail="Only the captain or an admin can edit this team")

    if body.name is not None:
        team.name = body.name.strip()[:128]

    if body.player_ids is not None:
        ids = [str(pid) for pid in body.player_ids]
        _validate_roster(team.format, ids)
        players = (await db.execute(select(Player).where(Player.id.in_(body.player_ids)))).scalars().all()
        if len(players) != len(ids):
            raise HTTPException(status_code=404, detail="One or more players were not found")
        existing = (await db.execute(select(TeamMember).where(TeamMember.team_id == team.id))).scalars().all()
        for member in existing:
            await db.delete(member)
        for pid in body.player_ids:
            db.add(TeamMember(team_id=team.id, player_id=pid))
        await db.flush()
        if team.captain_id and str(team.captain_id) not in ids:
            team.captain_id = body.player_ids[0]

    if body.captain_id is not None:
        member_ids = {str(m.player_id) for m in (await db.execute(
            select(TeamMember).where(TeamMember.team_id == team.id)
        )).scalars().all()}
        if str(body.captain_id) not in member_ids:
            raise HTTPException(status_code=400, detail="The captain must be on the roster")
        team.captain_id = body.captain_id

    await db.commit()
    return {"id": str(team.id), "name": _team_name(team)}


@router.delete("/{team_id}")
async def deactivate_team(
    team_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_user),
    __: None = Depends(csrf_protect),
):
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _user, player, is_admin = await _viewer(db, request)
    if not _can_manage(team, player, is_admin):
        raise HTTPException(status_code=403, detail="Only the captain or an admin can deactivate this team")
    team.is_active = False
    await db.commit()
    return {"status": "deactivated"}


def _team_name(team: Team) -> str:
    if team.name:
        return team.name
    names = [m.player.username for m in team.members if m.player]
    return " + ".join(names) if names else "Unknown Team"


@router.get("/{team_id}")
async def get_team(team_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
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
        .where(Match.status == "confirmed", or_(Match.team_a_id == team.id, Match.team_b_id == team.id))
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
    _user, viewer, is_admin = await _viewer(db, request)
    return {
        "id": str(team.id),
        "name": _team_name(team),
        "format": team.format,
        "captain": next((m.player.username for m in team.members if m.player and m.player_id == team.captain_id), None),
        "captain_id": str(team.captain_id) if team.captain_id else None,
        "is_active": team.is_active,
        "can_manage": _can_manage(team, viewer, is_admin),
        "elo": rating.elo if rating else 0,
        "rank_title": rank_title,
        "rank_color": rank_color,
        "wins": rating.wins if rating else 0,
        "losses": rating.losses if rating else 0,
        "matches_played": rating.matches_played if rating else 0,
        "streak": (rating.streak or 0) if rating else 0,
        "rd": round((rating.rd or 0) if rating else 0, 1),
        "provisional": ((rating.rd or 0) if rating else 0) > settings.provisional_rd,
        "form": form,
        "movement_7d": movement,
        "history": [
            {"elo": h.elo_after, "timestamp": h.changed_at.isoformat()}
            for h in history_rows
        ],
        "members": members,
        "recent_matches": recent,
    }
