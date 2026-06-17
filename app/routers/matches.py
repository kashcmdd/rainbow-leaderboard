from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import Optional

from app.database import get_db
from app.models import Player, Team, TeamMember, Match, Rating, RatingHistory, TournamentParticipant
from app.schemas import MatchReport, MatchOut
from app.config import settings
from app.elo import calculate_delta, calculate_delta_team
from app.ranks import recalculate_top_positions

router = APIRouter(prefix="/api/matches", tags=["matches"])


async def _resolve_players(db: AsyncSession, ids: list[str], names: list[str], discord_id: Optional[str] = None) -> list[Player]:
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


async def _get_or_create_rating(db: AsyncSession, player_id: str, format: str) -> Rating:
    result = await db.execute(
        select(Rating).where(Rating.player_id == player_id, Rating.format == format)
    )
    rating = result.scalar_one_or_none()
    if not rating:
        rating = Rating(player_id=player_id, format=format, elo=0)
        db.add(rating)
        await db.flush()
    return rating


async def _update_player_ratings(
    db: AsyncSession,
    player_ids: list[str],
    delta: int,
    is_winner: bool,
    format: str,
    match_id: str,
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
async def report_match(body: MatchReport, request: Request, db: AsyncSession = Depends(get_db)):
    winner = body.infer_winner()
    if winner is None:
        raise HTTPException(status_code=400, detail="Match cannot be a draw")

    session_user = request.session.get("user")
    reporter_did = session_user["id"] if session_user else None

    team_a = await _get_or_create_team(
        db, body.format,
        [str(p) for p in body.team_a_player_ids],
        body.team_a_player_names,
        discord_id=reporter_did,
    )
    team_b = await _get_or_create_team(
        db, body.format,
        [str(p) for p in body.team_b_player_ids],
        body.team_b_player_names,
        discord_id=reporter_did,
    )

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

    # Get resolved player IDs from the teams
    team_a_members = (await db.execute(
        select(Player).join(TeamMember).where(TeamMember.team_id == team_a.id)
    )).scalars().all()
    team_b_members = (await db.execute(
        select(Player).join(TeamMember).where(TeamMember.team_id == team_b.id)
    )).scalars().all()

    winner_players = team_a_members if winner == "a" else team_b_members
    loser_players = team_b_members if winner == "a" else team_a_members

    winner_ids = [str(p.id) for p in winner_players]
    loser_ids = [str(p.id) for p in loser_players]

    winner_ratings = [await _get_or_create_rating(db, pid, body.format) for pid in winner_ids]
    loser_ratings = [await _get_or_create_rating(db, pid, body.format) for pid in loser_ids]

    winner_avg = sum(r.elo for r in winner_ratings) // len(winner_ratings)
    loser_avg = sum(r.elo for r in loser_ratings) // len(loser_ratings)

    winner_total_matches = sum(r.matches_played for r in winner_ratings)
    loser_total_matches = sum(r.matches_played for r in loser_ratings)

    if body.format == "1v1":
        delta_winner, delta_loser = calculate_delta(
            winner_avg, loser_avg,
            body.score_a if winner == "a" else body.score_b,
            body.score_b if winner == "a" else body.score_a,
            winner_ratings[0].matches_played,
            loser_ratings[0].matches_played,
            format=body.format,
        )
    else:
        delta_winner, delta_loser = calculate_delta_team(
            winner_avg, loser_avg,
            body.score_a, body.score_b,
            winner_total_matches, loser_total_matches,
            format=body.format,
        )

    match.elo_delta_a = delta_winner if winner == "a" else delta_loser
    match.elo_delta_b = delta_loser if winner == "a" else delta_winner

    await _update_player_ratings(db, winner_ids, delta_winner, True, body.format, str(match.id))
    await _update_player_ratings(db, loser_ids, delta_loser, False, body.format, str(match.id))

    await db.commit()

    # Recalculate exclusive top 10 positions for 1v1
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


@router.get("", response_model=list[MatchOut])
async def list_matches(
    format: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    q = select(Match).options(
        selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
        selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
    ).order_by(Match.played_at.desc()).limit(limit)

    if format:
        q = q.where(Match.format == format)

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
