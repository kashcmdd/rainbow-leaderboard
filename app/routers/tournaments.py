import uuid
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_admin
from app.models import (
    Player, Team, TeamMember, Match, Rating, Tournament, TournamentParticipant, AuditLog
)
from app.ranks import get_rank
from app.csrf import csrf_protect

router = APIRouter(prefix="/api/tournaments", tags=["tournaments"])


async def _log_audit(db, admin_user, action, target_id=None, target_name=None, details=None):
    from app.models import AuditLog
    entry = AuditLog(
        admin_id=admin_user["id"],
        admin_name=admin_user["username"],
        action=action,
        target_id=target_id,
        target_name=target_name,
        details=details,
    )
    db.add(entry)
    await db.flush()


@router.get("")
async def list_tournaments(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Tournament).order_by(Tournament.created_at.desc())
    )
    tournaments = result.scalars().all()
    out = []
    for t in tournaments:
        participants = await db.execute(
            select(TournamentParticipant).where(TournamentParticipant.tournament_id == t.id)
        )
        p_list = participants.scalars().all()
        out.append({
            "id": str(t.id),
            "name": t.name,
            "status": t.status,
            "format": t.format,
            "player_count": len(p_list),
            "created_at": t.created_at.isoformat(),
        })
    return out


@router.get("/{tournament_id}")
async def get_tournament(tournament_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    tid = uuid.UUID(tournament_id)
    t = await db.get(Tournament, tid)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")

    participants = (await db.execute(
        select(TournamentParticipant)
        .where(TournamentParticipant.tournament_id == t.id)
        .order_by(TournamentParticipant.seed)
    )).scalars().all()

    p_list = []
    for p in participants:
        player = await db.get(Player, p.player_id)
        rating = (await db.execute(
            select(Rating).where(Rating.player_id == str(p.player_id), Rating.format == t.format)
        )).scalar_one_or_none()
        rank = get_rank(rating.elo if rating else 0)
        p_list.append({
            "id": str(p.player_id),
            "username": player.username if player else "Unknown",
            "seed": p.seed,
            "placement": p.placement,
            "elo": rating.elo if rating else 0,
            "rank_title": rank[0],
            "rank_color": rank[1],
        })

    matches = (await db.execute(
        select(Match)
        .where(Match.tournament_id == tournament_id)
        .options(
            selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
            selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
        )
        .order_by(Match.bracket_round, Match.bracket_position)
    )).scalars().all()

    m_list = []
    for m in matches:
        def team_name(team):
            if team.name:
                return team.name
            names = [mem.player.username for mem in team.members]
            return " + ".join(names) if names else "TBD"

        m_list.append({
            "id": str(m.id),
            "round": m.bracket_round,
            "position": m.bracket_position,
            "team_a_name": team_name(m.team_a),
            "team_b_name": team_name(m.team_b),
            "score_a": m.score_a,
            "score_b": m.score_b,
            "winner_name": team_name(m.team_a) if m.winner_team_id == m.team_a_id else team_name(m.team_b) if m.winner_team_id else None,
            "played": m.score_a != 0 or m.score_b != 0,
        })

    return {
        "id": str(t.id),
        "name": t.name,
        "status": t.status,
        "format": t.format,
        "created_at": t.created_at.isoformat(),
        "participants": p_list,
        "matches": m_list,
    }


@router.post("")
async def create_tournament(body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    name = body.get("name", "").strip()
    fmt = body.get("format", "1v1")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    t = Tournament(name=name, format=fmt)
    db.add(t)
    await _log_audit(db, admin_user, "create_tournament", target_name=name)
    await db.commit()
    await db.refresh(t)
    return {"id": str(t.id), "name": t.name, "status": t.status}


@router.post("/{tournament_id}/add-players")
async def add_tournament_players(tournament_id: uuid.UUID, body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    tid = uuid.UUID(tournament_id)
    t = await db.get(Tournament, tid)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    if t.status != "pending":
        raise HTTPException(status_code=400, detail="Can only add players to pending tournaments")

    player_ids = body.get("player_ids", [])
    for pid_str in player_ids:
        pid = uuid.UUID(pid_str)
        player = await db.get(Player, pid)
        if not player:
            continue
        existing = await db.execute(
            select(TournamentParticipant).where(
                TournamentParticipant.tournament_id == t.id,
                TournamentParticipant.player_id == pid,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(TournamentParticipant(tournament_id=t.id, player_id=pid))

    await _log_audit(db, admin_user, "add_tournament_players", target_name=t.name, details=f"{len(player_ids)} players")
    await db.commit()
    return {"status": "ok"}


@router.post("/{tournament_id}/start")
async def start_tournament(tournament_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    tid = uuid.UUID(tournament_id)
    t = await db.get(Tournament, tid)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    if t.status != "pending":
        raise HTTPException(status_code=400, detail="Tournament already started")

    participants = (await db.execute(
        select(TournamentParticipant)
        .where(TournamentParticipant.tournament_id == t.id)
    )).scalars().all()

    if len(participants) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 players")

    # Seed by ELO
    player_elo = []
    for p in participants:
        rating = (await db.execute(
            select(Rating).where(Rating.player_id == str(p.player_id), Rating.format == t.format)
        )).scalar_one_or_none()
        elo = rating.elo if rating else 0
        player_elo.append((p, elo))

    player_elo.sort(key=lambda x: -x[1])  # highest ELO first
    n = len(player_elo)
    next_pow2 = 2 ** math.ceil(math.log2(n))

    # Assign seeds
    for i, (p, _) in enumerate(player_elo):
        p.seed = i + 1

    # Generate bracket: highest seed vs lowest seed etc
    seeds = list(range(1, n + 1))
    # Pad to next power of 2 with byes
    while len(seeds) < next_pow2:
        seeds.append(None)

    # Create pairings for round 1 (standard bracket: 1 vs last, 2 vs second-last, etc)
    round1_matches = []
    for i in range(next_pow2 // 2):
        s1 = seeds[i]
        s2 = seeds[next_pow2 - 1 - i]
        round1_matches.append((s1, s2))

    # Create teams and match entries
    seed_map = {p.seed: p for p, _ in player_elo}
    num_rounds = int(math.log2(next_pow2))

    # Create a placeholder team for TBD slots
    def make_team():
        team = Team(format=t.format)
        db.add(team)
        return team

    # Round 1 matches — handle byes (auto-advance if opponent is None)
    round_matches = {}  # (round, position) -> match
    bye_advances = {}   # player -> (next_round, next_position, side)
    for pos, (s1, s2) in enumerate(round1_matches):
        if s1 is None and s2 is None:
            continue
        p1 = seed_map.get(s1) if s1 else None
        p2 = seed_map.get(s2) if s2 else None
        if not p1 or not p2:
            # Bye — auto-advance the player who has an opponent
            advancing_player = p1 or p2
            target_round = 1
            target_pos = pos
            side = "a" if p1 else "b"
            bye_advances[(target_round, target_pos)] = (advancing_player, side)
            continue

        team_a = make_team()
        await db.flush()
        db.add(TeamMember(team_id=team_a.id, player_id=p1.player_id))

        team_b = make_team()
        await db.flush()
        db.add(TeamMember(team_id=team_b.id, player_id=p2.player_id))

        match_entry = Match(
            format=t.format,
            tournament_id=str(t.id),
            bracket_round=1,
            bracket_position=pos,
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            score_a=0,
            score_b=0,
        )
        db.add(match_entry)
        await db.flush()
        round_matches[(1, pos)] = match_entry

    # Create placeholder matches for future rounds
    for rnd in range(2, num_rounds + 1):
        matches_in_round = next_pow2 // (2 ** rnd)
        for pos in range(matches_in_round):
            ta = make_team()
            tb = make_team()
            await db.flush()
            placeholder = Match(
                format=t.format,
                tournament_id=str(t.id),
                bracket_round=rnd,
                bracket_position=pos,
                team_a_id=ta.id,
                team_b_id=tb.id,
                score_a=0,
                score_b=0,
            )
            db.add(placeholder)
            await db.flush()
            round_matches[(rnd, pos)] = placeholder

    t.status = "active"

    # Auto-advance bye winners to the next round
    from app.ranks import recalculate_top_positions
    for (rnd, pos), (player, side) in bye_advances.items():
        next_pos = pos // 2
        next_match = round_matches.get((rnd + 1, next_pos))
        if next_match:
            target_team_id = next_match.team_a_id if side == "a" else next_match.team_b_id
            db.add(TeamMember(team_id=target_team_id, player_id=player.player_id))

    await _log_audit(db, admin_user, "start_tournament", target_name=t.name)
    await db.commit()
    return {"status": "started", "num_players": n, "num_rounds": num_rounds}


@router.post("/{tournament_id}/report-match")
async def report_tournament_match(tournament_id: uuid.UUID, body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    tid = uuid.UUID(tournament_id)
    t = await db.get(Tournament, tid)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    if t.status != "active":
        raise HTTPException(status_code=400, detail="Tournament is not active")

    match_id = body.get("match_id")
    score_a = body.get("score_a", 0)
    score_b = body.get("score_b", 0)
    if not match_id:
        raise HTTPException(status_code=400, detail="match_id required")

    match = await db.get(Match, uuid.UUID(match_id))
    if not match or match.tournament_id != tournament_id:
        raise HTTPException(status_code=404, detail="Match not found in this tournament")

    if score_a == score_b:
        raise HTTPException(status_code=400, detail="Scores cannot be tied")

    match.score_a = score_a
    match.score_b = score_b
    match.winner_team_id = match.team_a_id if score_a > score_b else match.team_b_id

    # Advance winner to next round
    next_round = (match.bracket_round or 1) + 1
    all_tmt_matches = await db.execute(
        select(Match).where(Match.tournament_id == tournament_id, Match.bracket_round.isnot(None))
    )
    all_m = all_tmt_matches.scalars().all()
    total_rounds = max((m.bracket_round or 0) for m in all_m) if all_m else 0

    if next_round <= total_rounds:
        next_pos = (match.bracket_position or 0) // 2
        existing = await db.execute(
            select(Match).where(
                Match.tournament_id == tournament_id,
                Match.bracket_round == next_round,
                Match.bracket_position == next_pos,
            )
        )
        next_match = existing.scalar_one_or_none()
        if next_match:
            winner_members = await db.execute(
                select(Player).join(TeamMember).where(TeamMember.team_id == match.winner_team_id)
            )
            for wp in winner_members.scalars().all():
                target_team_id = next_match.team_a_id if (match.bracket_position or 0) % 2 == 0 else next_match.team_b_id
                db.add(TeamMember(team_id=target_team_id, player_id=wp.id))
    else:
        # Final match completed
        t.status = "completed"
        winning_team = match.team_a if match.winner_team_id == match.team_a_id else match.team_b
        if winning_team:
            members = (await db.execute(
                select(Player).join(TeamMember).where(TeamMember.team_id == winning_team.id)
            )).scalars().all()
            winner_name = " + ".join(m.username for m in members) if members else "Unknown"
            await _log_audit(db, admin_user, "tournament_winner", target_name=winner_name, details=f"Won tournament: {t.name}")

    await _log_audit(db, admin_user, "report_tournament_match", target_name=t.name, details=f"Match {match_id}: {score_a}-{score_b}")
    await db.commit()
    return {"status": "ok"}


@router.post("/{tournament_id}/complete")
async def complete_tournament(tournament_id: uuid.UUID, body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    tid = uuid.UUID(tournament_id)
    t = await db.get(Tournament, tid)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")

    t.status = "completed"

    # Find winner from final match
    final_matches = (await db.execute(
        select(Match)
        .where(Match.tournament_id == tournament_id, Match.bracket_round.isnot(None))
        .order_by(desc(Match.bracket_round), desc(Match.bracket_position))
        .limit(1)
    )).scalars().all()

    winner_name = None
    if final_matches:
        fm = final_matches[0]
        winning_team = fm.team_a if fm.winner_team_id == fm.team_a_id else fm.team_b
        if winning_team:
            members = (await db.execute(
                select(Player).join(TeamMember).where(TeamMember.team_id == winning_team.id)
            )).scalars().all()
            winner_name = " + ".join(m.username for m in members) if members else "Unknown"
            for m in members:
                placement = body.get("placement", {})
                if str(m.id) in placement:
                    await db.execute(
                        TournamentParticipant.__table__.update()
                        .where(TournamentParticipant.tournament_id == t.id, TournamentParticipant.player_id == m.id)
                        .values(placement=placement[str(m.id)])
                    )

    await _log_audit(db, admin_user, "complete_tournament", target_name=t.name, details=f"Winner: {winner_name}")
    if winner_name:
        await _log_audit(db, admin_user, "tournament_winner", target_name=winner_name, details=f"Won tournament: {t.name}")

    await db.commit()
    return {"status": "completed", "winner": winner_name}


@router.delete("/{tournament_id}")
async def delete_tournament(tournament_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    tid = uuid.UUID(tournament_id)
    t = await db.get(Tournament, tid)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    await db.delete(t)
    await _log_audit(db, admin_user, "delete_tournament", target_name=t.name)
    await db.commit()
    return {"status": "deleted"}
