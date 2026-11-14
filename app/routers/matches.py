import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.csrf import csrf_protect
from app.database import get_db
from app.elo import calculate_delta, calculate_delta_team
from app.models import Match, Player, Rating, RatingHistory, Team, TeamMember
from app.ranks import recalculate_top_positions
from app.ratelimit import rate_limit
from app.schemas import MatchDetail, MatchOut, MatchReport

router = APIRouter(prefix="/api/matches", tags=["matches"])
logger = logging.getLogger(__name__)


async def _resolve_players(db: AsyncSession, ids: list[str], names: list[str], discord_id: Optional[str] = None, create: bool = True) -> list[Player]:
    players = []
    for pid in ids:
        result = await db.execute(select(Player).where(Player.id == pid))
        p = result.scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail=f"Player ID {pid} not found")
        players.append(p)
    for name in names:
        result = await db.execute(select(Player).where(Player.username == name))
        p = result.scalar_one_or_none()
        if not p:
            if not create:
                raise HTTPException(status_code=404, detail=f"Player '{name}' not found")
            p = Player(username=name, discord_id=discord_id)
            db.add(p)
            await db.flush()
        players.append(p)
    return players


async def _get_or_create_team(db: AsyncSession, format: str, player_ids: list[str], player_names: list[str], discord_id: Optional[str] = None, name: Optional[str] = None) -> Team:
    players = await _resolve_players(db, player_ids, player_names, discord_id)

    team = Team(format=format, name=name)
    db.add(team)
    await db.flush()

    for p in players:
        db.add(TeamMember(team_id=team.id, player_id=p.id))
    await db.flush()

    return team


async def _get_or_create_rating(db: AsyncSession, player_id: uuid.UUID, format: str) -> Rating:
    result = await db.execute(
        select(Rating).where(Rating.player_id == player_id, Rating.format == format)
    )
    rating = result.scalar_one_or_none()
    if not rating:
        rating = Rating(player_id=player_id, format=format, elo=0)
        db.add(rating)
        await db.flush()
    return rating


async def _rating_for(db: AsyncSession, player_id: str, format: str, create: bool) -> Rating:
    """Fetch a rating, optionally creating it; in preview mode return a transient 0."""
    if create:
        return await _get_or_create_rating(db, player_id, format)
    result = await db.execute(
        select(Rating).where(Rating.player_id == player_id, Rating.format == format)
    )
    rating = result.scalar_one_or_none()
    if rating is None:
        rating = Rating(player_id=player_id, format=format, elo=0, wins=0, losses=0, matches_played=0)
    return rating


def _validate_teams(format: str, team_a: list[Player], team_b: list[Player]) -> None:
    try:
        expected = int(format.split("v")[0])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid format") from None
    if len(team_a) != expected or len(team_b) != expected:
        raise HTTPException(
            status_code=400,
            detail=f"{format} requires exactly {expected} player(s) per team",
        )
    if {p.id for p in team_a} & {p.id for p in team_b}:
        raise HTTPException(status_code=400, detail="A player cannot be on both teams")


async def _compute_deltas(
    db: AsyncSession,
    format: str,
    winner_players: list[Player],
    loser_players: list[Player],
    winner_score: int,
    loser_score: int,
    create: bool = False,
) -> tuple[int, int]:
    winner_ratings = [await _rating_for(db, str(p.id), format, create) for p in winner_players]
    loser_ratings = [await _rating_for(db, str(p.id), format, create) for p in loser_players]
    winner_avg = sum(r.elo for r in winner_ratings) // len(winner_ratings)
    loser_avg = sum(r.elo for r in loser_ratings) // len(loser_ratings)
    if format == "1v1":
        return calculate_delta(
            winner_avg, loser_avg, winner_score, loser_score,
            winner_ratings[0].matches_played, loser_ratings[0].matches_played,
            format=format,
        )
    return calculate_delta_team(
        winner_avg, loser_avg, winner_score, loser_score,
        max(r.matches_played for r in winner_ratings),
        max(r.matches_played for r in loser_ratings),
        format=format,
    )


async def _find_duplicate(
    db: AsyncSession,
    format: str,
    team_a_ids: set[str],
    team_b_ids: set[str],
    score_a: int,
    score_b: int,
    window_minutes: int = 15,
) -> Optional[Match]:
    """Same teams, same score, same format inside the window = a double submit."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    recent = (await db.execute(
        select(Match)
        .options(
            selectinload(Match.team_a).selectinload(Team.members),
            selectinload(Match.team_b).selectinload(Team.members),
        )
        .where(Match.format == format, Match.played_at >= cutoff)
    )).scalars().all()
    for m in recent:
        if m.score_a != score_a or m.score_b != score_b:
            continue
        a_ids = {str(x.player_id) for x in m.team_a.members}
        b_ids = {str(x.player_id) for x in m.team_b.members}
        if a_ids == team_a_ids and b_ids == team_b_ids:
            return m
    return None


async def _find_or_create_team(db: AsyncSession, format: str, players: list[Player]) -> Team:
    """Reuse the team row for an identical roster so team ratings accumulate."""
    player_ids = {str(p.id) for p in players}
    existing = (await db.execute(
        select(Team)
        .options(selectinload(Team.members))
        .where(Team.format == format)
    )).scalars().all()
    for team in existing:
        if {str(m.player_id) for m in team.members} == player_ids:
            return team
    team = Team(format=format)
    db.add(team)
    await db.flush()
    for p in players:
        db.add(TeamMember(team_id=team.id, player_id=p.id))
    await db.flush()
    return team


async def _update_team_rating(
    db: AsyncSession, team_id: uuid.UUID, delta: int, is_winner: bool, format: str
) -> None:
    rating = (await db.execute(
        select(Rating).where(Rating.team_id == team_id, Rating.format == format)
    )).scalar_one_or_none()
    if not rating:
        rating = Rating(team_id=team_id, format=format, elo=0)
        db.add(rating)
        await db.flush()
    rating.elo = max(rating.elo + delta, settings.rating_floor)
    rating.matches_played += 1
    if is_winner:
        rating.wins += 1
        rating.streak = (rating.streak + 1) if rating.streak > 0 else 1
    else:
        rating.losses += 1
        rating.streak = (rating.streak - 1) if rating.streak < 0 else -1
    rating.last_updated = datetime.now(timezone.utc)


async def _notify_discord(message: str) -> None:
    url = settings.discord_webhook_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"content": message})
    except Exception:
        logger.warning("Discord webhook failed", exc_info=True)


async def _update_player_ratings(
    db: AsyncSession,
    player_ids: list[str],
    delta: int,
    is_winner: bool,
    format: str,
    match_id: uuid.UUID,
):
    for pid in player_ids:
        rating = await _get_or_create_rating(db, pid, format)
        old_elo = rating.elo
        rating.elo = max(rating.elo + delta, settings.rating_floor)
        rating.matches_played += 1
        if is_winner:
            rating.wins += 1
            rating.streak = (rating.streak + 1) if rating.streak > 0 else 1
        else:
            rating.losses += 1
            rating.streak = (rating.streak - 1) if rating.streak < 0 else -1
        rating.is_decaying = False
        from datetime import datetime, timezone
        rating.last_active = datetime.now(timezone.utc)
        rating.last_updated = datetime.now(timezone.utc)

        db.add(RatingHistory(
            rating_id=rating.id,
            player_id=pid,
            elo_before=old_elo,
            elo_after=rating.elo,
            match_id=match_id,
        ))
    await db.flush()


@router.post("")
async def report_match(
    body: MatchReport,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
    _rl: None = Depends(rate_limit(10, 60)),
):
    session_user = request.session.get("user")
    if not session_user:
        raise HTTPException(status_code=401, detail="Not logged in")

    # Check if reporter is banned
    reporter_result = await db.execute(select(Player).where(Player.discord_id == session_user["id"]))
    reporter_player = reporter_result.scalar_one_or_none()
    if reporter_player and reporter_player.is_banned:
        raise HTTPException(status_code=403, detail="You are banned from reporting matches")

    winner = body.infer_winner()
    if winner is None:
        raise HTTPException(status_code=400, detail="Match cannot be a draw")

    reporter_did = session_user["id"]
    team_a_players = await _resolve_players(
        db, [str(p) for p in body.team_a_player_ids], body.team_a_player_names,
        discord_id=reporter_did,
    )
    team_b_players = await _resolve_players(
        db, [str(p) for p in body.team_b_player_ids], body.team_b_player_names,
        discord_id=reporter_did,
    )
    _validate_teams(body.format, team_a_players, team_b_players)

    duplicate = await _find_duplicate(
        db, body.format,
        {str(p.id) for p in team_a_players}, {str(p.id) for p in team_b_players},
        body.score_a, body.score_b,
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"This match was already reported a moment ago (match {duplicate.id})",
        )

    team_a = await _find_or_create_team(db, body.format, team_a_players)
    team_b = await _find_or_create_team(db, body.format, team_b_players)

    match = Match(
        format=body.format,
        team_a_id=team_a.id,
        team_b_id=team_b.id,
        score_a=body.score_a,
        score_b=body.score_b,
        winner_team_id=team_a.id if winner == "a" else team_b.id,
    )
    db.add(match)
    await db.flush()

    winner_players = team_a_players if winner == "a" else team_b_players
    loser_players = team_b_players if winner == "a" else team_a_players
    winner_score = body.score_a if winner == "a" else body.score_b
    loser_score = body.score_b if winner == "a" else body.score_a

    delta_winner, delta_loser = await _compute_deltas(
        db, body.format, winner_players, loser_players,
        winner_score, loser_score, create=True,
    )

    match.elo_delta_a = delta_winner if winner == "a" else delta_loser
    match.elo_delta_b = delta_loser if winner == "a" else delta_winner

    await _update_player_ratings(
        db, [str(p.id) for p in winner_players], delta_winner, True, body.format, str(match.id)
    )
    await _update_player_ratings(
        db, [str(p.id) for p in loser_players], delta_loser, False, body.format, str(match.id)
    )
    await _update_team_rating(db, team_a.id, delta_winner if winner == "a" else delta_loser,
                              winner == "a", body.format)
    await _update_team_rating(db, team_b.id, delta_winner if winner == "b" else delta_loser,
                              winner == "b", body.format)

    await db.commit()

    # Exclusive top 10 positions are tracked for 1v1 only.
    if body.format == "1v1":
        await recalculate_top_positions(db)
        await db.commit()

    # Tournament bracket advancement
    if match.tournament_id and match.winner_team_id:
        next_round = (match.bracket_round or 1) + 1
        next_pos = (match.bracket_position or 0) // 2

        existing = await db.execute(
            select(Match).where(
                Match.tournament_id == match.tournament_id,
                Match.bracket_round == next_round,
                Match.bracket_position == next_pos,
            )
        )
        next_match = existing.scalar_one_or_none()

        if next_match:
            winner_members = await db.execute(
                select(Player).join(TeamMember).where(TeamMember.team_id == match.winner_team_id)
            )
            winner_player_ids = [wp.id for wp in winner_members.scalars().all()]

            # Even bracket_position goes to team_a, odd goes to team_b
            target_team_id = next_match.team_a_id if (match.bracket_position or 0) % 2 == 0 else next_match.team_b_id
            for pid in winner_player_ids:
                db.add(TeamMember(team_id=target_team_id, player_id=pid))
            await db.commit()

    if settings.discord_webhook_url:
        winners = " + ".join(p.username for p in winner_players)
        losers = " + ".join(p.username for p in loser_players)
        message = f"{winners} won {winner_score}-{loser_score} vs {losers} ({body.format})"
        if settings.public_base_url:
            message += f" {settings.public_base_url.rstrip('/')}/match/{match.id}"
        background_tasks.add_task(_notify_discord, message)

    first_winner_id = str(winner_players[0].id) if winner_players else None

    return {
        "match_id": str(match.id),
        "winner": "team_a" if winner == "a" else "team_b",
        "score": f"{body.score_a}-{body.score_b}",
        "winner_player_id": first_winner_id,
        "elo_deltas": {
            "winner_delta": delta_winner,
            "loser_delta": delta_loser,
        },
    }


@router.post("/preview")
async def preview_match(
    body: MatchReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
    _rl: None = Depends(rate_limit(30, 60)),
):
    """Compute the ELO swing for a proposed result without writing anything."""
    session_user = request.session.get("user")
    if not session_user:
        raise HTTPException(status_code=401, detail="Not logged in")

    winner = body.infer_winner()
    if winner is None:
        raise HTTPException(status_code=400, detail="Match cannot be a draw")

    team_a_players = await _resolve_players(
        db, [str(p) for p in body.team_a_player_ids], body.team_a_player_names, create=False
    )
    team_b_players = await _resolve_players(
        db, [str(p) for p in body.team_b_player_ids], body.team_b_player_names, create=False
    )
    _validate_teams(body.format, team_a_players, team_b_players)

    duplicate = await _find_duplicate(
        db, body.format,
        {str(p.id) for p in team_a_players}, {str(p.id) for p in team_b_players},
        body.score_a, body.score_b,
    )

    winner_players = team_a_players if winner == "a" else team_b_players
    loser_players = team_b_players if winner == "a" else team_a_players
    winner_score = body.score_a if winner == "a" else body.score_b
    loser_score = body.score_b if winner == "a" else body.score_a

    delta_winner, delta_loser = await _compute_deltas(
        db, body.format, winner_players, loser_players,
        winner_score, loser_score, create=False,
    )

    return {
        "team_a": " + ".join(p.username for p in team_a_players),
        "team_b": " + ".join(p.username for p in team_b_players),
        "winner": "team_a" if winner == "a" else "team_b",
        "duplicate": duplicate is not None,
        "duplicate_match_id": str(duplicate.id) if duplicate else None,
        "elo_deltas": {
            "winner_delta": delta_winner,
            "loser_delta": delta_loser,
        },
    }


@router.get("", response_model=list[MatchOut])
async def list_matches(
    format: Optional[str] = None,
    player_id: Optional[str] = None,
    limit: int = 50,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    opponent_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Match).options(
        selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
        selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
    ).order_by(Match.played_at.desc()).limit(limit)

    if format:
        q = q.where(Match.format == format)

    if date_from:
        q = q.where(Match.played_at >= f"{date_from}T00:00:00")
    if date_to:
        q = q.where(Match.played_at <= f"{date_to}T23:59:59")

    if opponent_id:
        opponent_team_ids = select(TeamMember.team_id).where(TeamMember.player_id == opponent_id)
        if player_id:
            player_team_ids = select(TeamMember.team_id).where(TeamMember.player_id == player_id)
            q = q.where(
                ((Match.team_a_id.in_(player_team_ids)) & (Match.team_b_id.in_(opponent_team_ids))) |
                ((Match.team_a_id.in_(opponent_team_ids)) & (Match.team_b_id.in_(player_team_ids)))
            )
        else:
            q = q.where(
                Match.team_a_id.in_(opponent_team_ids) |
                Match.team_b_id.in_(opponent_team_ids)
            )

    if player_id:
        q = q.where(
            Match.team_a_id.in_(
                select(TeamMember.team_id).where(TeamMember.player_id == player_id)
            ) |
            Match.team_b_id.in_(
                select(TeamMember.team_id).where(TeamMember.player_id == player_id)
            )
        )

    result = await db.execute(q)
    matches = result.scalars().all()

    out = []
    for m in matches:
        def team_name(team):
            if team.name:
                return team.name
            return " + ".join(mem.player.username for mem in team.members)

        a_name = team_name(m.team_a)
        b_name = team_name(m.team_b)
        w_name = a_name if m.winner_team_id == m.team_a_id else b_name if m.winner_team_id else None

        out.append(MatchOut(
            id=m.id,
            format=m.format,
            score_a=m.score_a,
            score_b=m.score_b,
            played_at=m.played_at,
            team_a_name=a_name,
            team_b_name=b_name,
            winner_name=w_name,
            elo_delta_a=m.elo_delta_a,
            elo_delta_b=m.elo_delta_b,
        ))
    return out


@router.get("/{match_id}", response_model=MatchDetail)
async def get_match(match_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    match = (await db.execute(
        select(Match)
        .options(
            selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
            selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
        )
        .where(Match.id == match_id)
    )).scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    def team_name(team):
        if team.name:
            return team.name
        return " + ".join(mem.player.username for mem in team.members)

    a_name = team_name(match.team_a)
    b_name = team_name(match.team_b)
    winner_name = a_name if match.winner_team_id == match.team_a_id else b_name if match.winner_team_id else None

    return MatchDetail(
        id=match.id,
        format=match.format,
        score_a=match.score_a,
        score_b=match.score_b,
        played_at=match.played_at,
        team_a_name=a_name,
        team_b_name=b_name,
        team_a_players=[m.player.username for m in match.team_a.members if m.player],
        team_b_players=[m.player.username for m in match.team_b.members if m.player],
        winner_name=winner_name,
        elo_delta_a=match.elo_delta_a,
        elo_delta_b=match.elo_delta_b,
        tournament_id=match.tournament_id,
    )
