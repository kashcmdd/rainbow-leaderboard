import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import select, func, delete as sa_delete, update as sa_update, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pathlib import Path

from app.config import settings
from app.database import get_db
from app.deps import require_admin
from app.models import Player, Rating, RatingHistory, Match, TeamMember, Team, AuditLog, Season, SeasonSnapshot
from app.ranks import RANKS, get_rank
from app.csrf import csrf_protect
from app.schemas_admin import NotesUpdate, MatchUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])

AVATAR_DIR = Path(__file__).parent.parent / "static" / "avatars"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024


async def _log_audit(db: AsyncSession, admin_user: dict, action: str, target_id: str = None, target_name: str = None, details: str = None):
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


@router.get("/check")
async def check_admin(request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        return {"is_admin": False}
    discord_id = user["id"]
    is_admin = discord_id in settings.admin_discord_ids
    if not is_admin:
        result = await db.execute(select(Player).where(Player.discord_id == discord_id))
        player = result.scalar_one_or_none()
        is_admin = player is not None and player.is_admin
    if user.get("is_admin") != is_admin:
        user["is_admin"] = is_admin
        request.session["user"] = user
    return {"is_admin": is_admin}


@router.get("/stats")
async def get_admin_stats(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    total_players = (await db.execute(select(func.count(Player.id)))).scalar() or 0
    total_matches = (await db.execute(select(func.count(Match.id)))).scalar() or 0
    total_ratings = (await db.execute(select(func.count(Rating.id)))).scalar() or 0

    avg_elo = (await db.execute(select(func.avg(Rating.elo)))).scalar() or 0
    highest_elo = (await db.execute(select(func.max(Rating.elo)))).scalar() or 0

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    active_player_ids = set()
    recent_matches = await db.execute(
        select(Match).where(Match.played_at >= seven_days_ago)
    )
    for m in recent_matches.scalars().all():
        members_a = await db.execute(
            select(Player).join(TeamMember).where(TeamMember.team_id == m.team_a_id)
        )
        for p in members_a.scalars().all():
            active_player_ids.add(str(p.id))
        members_b = await db.execute(
            select(Player).join(TeamMember).where(TeamMember.team_id == m.team_b_id)
        )
        for p in members_b.scalars().all():
            active_player_ids.add(str(p.id))

    distribution = {name: 0 for name, _, _ in RANKS}
    all_ratings = (await db.execute(select(Rating.elo))).scalars().all()
    for elo in all_ratings:
        for name, threshold, _ in reversed(RANKS):
            if elo >= threshold:
                distribution[name] += 1
                break
        else:
            distribution["Unranked"] = distribution.get("Unranked", 0) + 1

    rank_color_map = {name: color for name, _, color in RANKS}
    return {
        "total_players": total_players,
        "total_matches": total_matches,
        "total_ratings": total_ratings,
        "active_players": len(active_player_ids),
        "avg_elo": round(avg_elo),
        "highest_elo": highest_elo,
        "owner_ids": settings.admin_discord_ids,
        "distribution": [{"rank": k, "count": v, "color": rank_color_map.get(k, "#666666")} for k, v in distribution.items() if v > 0],
    }


@router.get("/matches")
async def get_recent_matches(limit: int = 50, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    result = await db.execute(
        select(Match)
        .options(
            selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
            selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
        )
        .order_by(Match.played_at.desc())
        .limit(limit)
    )
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
        out.append({
            "id": str(m.id),
            "format": m.format,
            "score_a": m.score_a,
            "score_b": m.score_b,
            "team_a_name": a_name,
            "team_b_name": b_name,
            "winner_name": w_name,
            "played_at": m.played_at.isoformat(),
        })
    return out


@router.delete("/matches/{match_id}")
async def delete_match(match_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin), __: None = Depends(csrf_protect)):
    match_uuid = uuid.UUID(match_id)
    await db.execute(sa_delete(RatingHistory).where(RatingHistory.match_id == match_uuid))
    result = await db.execute(select(Match).where(Match.id == match_uuid))
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    await db.delete(match)
    await db.commit()
    return {"status": "deleted"}


@router.post("/bulk/reset-stats")
async def bulk_reset_stats(body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    player_ids = [uuid.UUID(pid) for pid in body.get("player_ids", [])]
    for pid in player_ids:
        team_ids_subq = select(TeamMember.team_id).where(TeamMember.player_id == pid).subquery()
        match_ids_result = await db.execute(
            select(Match.id).where(
                or_(Match.team_a_id.in_(team_ids_subq), Match.team_b_id.in_(team_ids_subq))
            )
        )
        match_ids = [row[0] for row in match_ids_result.all()]
        if match_ids:
            await db.execute(sa_delete(RatingHistory).where(RatingHistory.match_id.in_(match_ids)))
            for mid in match_ids:
                await db.execute(sa_delete(Match).where(Match.id == mid))
        ratings = await db.execute(select(Rating).where(Rating.player_id == pid))
        for r in ratings.scalars().all():
            r.elo = 0; r.wins = 0; r.losses = 0; r.matches_played = 0
    await db.commit()
    return {"status": "ok", "reset_count": len(player_ids)}


@router.post("/bulk/delete")
async def bulk_delete_players(body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    player_ids = body.get("player_ids", [])
    for pid_str in player_ids:
        pid = uuid.UUID(pid_str)
        player = await db.get(Player, pid)
        if player:
            await _log_audit(db, admin_user, "bulk_delete_player", target_id=pid_str, target_name=player.username)
            await db.delete(player)
    await db.commit()
    return {"status": "ok", "deleted_count": len(player_ids)}


@router.post("/bulk/clear-avatar")
async def bulk_clear_avatar(body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    player_ids = body.get("player_ids", [])
    for pid_str in player_ids:
        pid = uuid.UUID(pid_str)
        player = await db.get(Player, pid)
        if player:
            player.avatar_url = None
            await _log_audit(db, admin_user, "clear_avatar", target_id=pid_str, target_name=player.username)
    await db.commit()
    return {"status": "ok", "cleared_count": len(player_ids)}


@router.post("/clear-leaderboard")
async def clear_leaderboard(db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    await db.execute(sa_delete(RatingHistory))
    await db.execute(sa_delete(Match))
    await db.execute(sa_delete(Rating))
    await _log_audit(db, admin_user, "clear_leaderboard")
    await db.commit()
    return {"status": "cleared"}


@router.post("/reset-leaderboard")
async def reset_leaderboard(db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    await db.execute(sa_delete(RatingHistory))
    await db.execute(sa_delete(Match))
    await db.execute(sa_update(Rating).values(elo=0, wins=0, losses=0, matches_played=0))
    await _log_audit(db, admin_user, "reset_leaderboard")
    await db.commit()
    return {"status": "reset"}


@router.post("/players/{player_id}/reset-stats")
async def reset_player_stats(player_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    result = await db.execute(select(Rating).where(Rating.player_id == player_id))
    ratings = result.scalars().all()
    if not ratings:
        raise HTTPException(status_code=404, detail="No ratings found for this player")

    team_ids_subq = select(TeamMember.team_id).where(TeamMember.player_id == player_id).subquery()
    match_ids_result = await db.execute(
        select(Match.id).where(
            or_(Match.team_a_id.in_(team_ids_subq), Match.team_b_id.in_(team_ids_subq))
        )
    )
    match_ids = [row[0] for row in match_ids_result.all()]
    if match_ids:
        await db.execute(sa_delete(RatingHistory).where(RatingHistory.match_id.in_(match_ids)))
        for mid in match_ids:
            await db.execute(sa_delete(Match).where(Match.id == mid))

    for r in ratings:
        r.elo = 0; r.wins = 0; r.losses = 0; r.matches_played = 0
    await _log_audit(db, admin_user, "reset_player_stats", target_id=player_id)
    await db.commit()
    return {"status": "reset"}


@router.post("/clear-all-player-stats")
async def clear_all_player_stats(db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    await db.execute(sa_delete(RatingHistory))
    await db.execute(sa_delete(Match))
    await db.execute(sa_update(Rating).values(elo=0, wins=0, losses=0, matches_played=0))
    await _log_audit(db, admin_user, "clear_all_player_stats")
    await db.commit()
    return {"status": "cleared"}


@router.post("/players/{player_id}/toggle-admin")
async def toggle_admin(player_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    pid = uuid.UUID(player_id)
    player = await db.get(Player, pid)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    if player.discord_id in settings.admin_discord_ids:
        raise HTTPException(status_code=403, detail="Cannot change owner's admin status")
    player.is_admin = not player.is_admin
    status = "granted" if player.is_admin else "revoked"
    await _log_audit(db, admin_user, f"admin_{status}", target_id=player_id, target_name=player.username)
    await db.commit()
    return {"status": "ok", "is_admin": player.is_admin, "player_id": player_id, "username": player.username}


@router.get("/audit-log")
async def get_audit_log(limit: int = 100, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    )
    entries = result.scalars().all()
    return [{
        "id": str(e.id),
        "admin_name": e.admin_name,
        "action": e.action,
        "target_id": e.target_id,
        "target_name": e.target_name,
        "details": e.details,
        "created_at": e.created_at.isoformat(),
    } for e in entries]


@router.post("/clear-audit-log")
async def clear_audit_log(db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    await db.execute(sa_delete(AuditLog))
    await _log_audit(db, admin_user, "clear_audit_log")
    await db.commit()
    return {"status": "cleared"}


@router.post("/players/{player_id}/adjust-elo")
async def adjust_elo(player_id: uuid.UUID, body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    pid = uuid.UUID(player_id)
    player = await db.get(Player, pid)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    amount = body.get("amount")
    reason = body.get("reason", "").strip()
    if amount is None or not isinstance(amount, int):
        raise HTTPException(status_code=400, detail="Invalid amount")
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required")

    rating = (await db.execute(
        select(Rating).where(Rating.player_id == player_id, Rating.format == "1v1")
    )).scalar_one_or_none()
    if not rating:
        raise HTTPException(status_code=404, detail="No 1v1 rating found for this player")

    old_elo = rating.elo
    rating.elo = max(rating.elo + amount, 0)
    await _log_audit(db, admin_user, "elo_adjustment",
                     target_id=player_id, target_name=player.username,
                     details=f"{old_elo} -> {rating.elo} ({amount:+d}) - {reason}")
    await db.commit()
    return {"status": "ok", "old_elo": old_elo, "new_elo": rating.elo, "amount": amount}


@router.post("/players/{player_id}/toggle-ban")
async def toggle_ban(player_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    pid = uuid.UUID(player_id)
    player = await db.get(Player, pid)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    if player.discord_id in settings.admin_discord_ids:
        raise HTTPException(status_code=403, detail="Cannot ban the owner")
    player.is_banned = not player.is_banned
    status = "banned" if player.is_banned else "unbanned"
    await _log_audit(db, admin_user, f"player_{status}", target_id=player_id, target_name=player.username)
    await db.commit()
    return {"status": "ok", "is_banned": player.is_banned, "player_id": player_id, "username": player.username}


@router.post("/players/{player_id}/avatar")
async def admin_set_avatar(player_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    pid = uuid.UUID(player_id)
    player = await db.get(Player, pid)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    form = await request.form()
    file = form.get("file")
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 2MB)")

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{player_id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = AVATAR_DIR / filename
    filepath.write_bytes(contents)
    avatar_url = str(request.base_url) + f"static/avatars/{filename}"
    player.avatar_url = avatar_url
    await _log_audit(db, admin_user, "set_avatar", target_id=player_id, target_name=player.username)
    await db.commit()
    return {"status": "ok", "avatar_url": avatar_url}


@router.get("/seasons")
async def list_seasons(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    result = await db.execute(
        select(Season).order_by(Season.start_date.desc())
    )
    seasons = result.scalars().all()
    out = []
    for s in seasons:
        snapshot_count = (await db.execute(
            select(func.count(SeasonSnapshot.id)).where(SeasonSnapshot.season_id == s.id)
        )).scalar() or 0
        out.append({
            "id": str(s.id),
            "name": s.name,
            "status": s.status,
            "start_date": s.start_date.isoformat(),
            "end_date": s.end_date.isoformat() if s.end_date else None,
            "player_count": snapshot_count,
        })
    return out


@router.get("/seasons/{season_id}")
async def get_season(season_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
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


@router.post("/close-season")
async def close_season(body: dict, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    name = body.get("name", "Season")
    result = await db.execute(
        select(Season).where(Season.status == "active").limit(1)
    )
    current = result.scalar_one_or_none()
    if not current:
        current = Season(name=name, status="active")
        db.add(current)
        await db.flush()

    # Snapshot all players
    ratings = (await db.execute(
        select(Rating)
        .where(Rating.format == "1v1", Rating.player_id.isnot(None))
        .order_by(Rating.elo.desc())
    )).scalars().all()

    for pos, r in enumerate(ratings, 1):
        p = await db.get(Player, r.player_id)
        rank_title, rank_color = get_rank(r.elo, r.top_position)
        db.add(SeasonSnapshot(
            season_id=current.id,
            player_id=r.player_id,
            player_name=p.username if p else "Unknown",
            elo=r.elo,
            rank_title=rank_title,
            rank_color=rank_color,
            wins=r.wins,
            losses=r.losses,
            position=pos,
        ))

    current.status = "archived"
    current.end_date = datetime.now(timezone.utc)

    # Create new season with incremented name
    existing_seasons = await db.execute(select(Season))
    season_count = len(existing_seasons.scalars().all())
    new_name = f"{name} {season_count + 1}" if name != "Season" else f"Season {season_count + 1}"
    new_season = Season(name=new_name, status="active")
    db.add(new_season)

    # Reset all ratings
    for r in ratings:
        r.elo = 0; r.wins = 0; r.losses = 0; r.matches_played = 0; r.top_position = None

    await _log_audit(db, admin_user, "close_season", target_name=name)
    await db.commit()
    return {"status": "closed", "archived_season": str(current.id), "new_season": str(new_season.id)}


@router.delete("/players/{player_id}/avatar")
async def admin_remove_avatar(player_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin_user: dict = Depends(require_admin), _: None = Depends(csrf_protect)):
    pid = uuid.UUID(player_id)
    player = await db.get(Player, pid)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    player.avatar_url = None
    await _log_audit(db, admin_user, "remove_avatar", target_id=player_id, target_name=player.username)
    await db.commit()
    return {"status": "ok"}


@router.get("/players/{player_id}")
async def admin_get_player(player_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    pid = uuid.UUID(player_id)
    player = await db.get(Player, pid)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return {
        "id": str(player.id),
        "username": player.username,
        "discord_id": player.discord_id,
        "avatar_url": player.avatar_url,
        "is_admin": player.is_admin,
        "is_banned": player.is_banned,
        "notes": player.notes,
        "created_at": player.created_at.isoformat(),
    }


@router.put("/players/{player_id}/notes")
async def admin_set_notes(player_id: uuid.UUID, body: NotesUpdate, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin), __: None = Depends(csrf_protect)):
    pid = uuid.UUID(player_id)
    player = await db.get(Player, pid)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    player.notes = body.notes
    await db.commit()
    return {"status": "ok", "notes": player.notes}


@router.post("/players/import")
async def admin_import_players(file: UploadFile = File(...), db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin), __: None = Depends(csrf_protect)):
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files accepted")
    contents = await file.read()
    text = contents.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    created = 0
    skipped = 0
    errors = []
    seen = set()
    for row in reader:
        if not row:
            continue
        username = row[0].strip()
        if not username:
            continue
        if username.lower() in seen:
            skipped += 1
            continue
        seen.add(username.lower())
        existing = await db.execute(select(Player).where(Player.username == username))
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        try:
            db.add(Player(username=username))
            created += 1
        except Exception as e:
            errors.append(f"{username}: {str(e)}")
    await db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


@router.patch("/matches/{match_id}")
async def admin_update_match(match_id: uuid.UUID, body: MatchUpdate, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin), __: None = Depends(csrf_protect)):
    mid = uuid.UUID(match_id)
    match = await db.get(Match, mid)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    match.score_a = body.score_a
    match.score_b = body.score_b
    match.winner_team_id = match.team_a_id if body.winner == "a" else match.team_b_id
    await db.commit()
    return {"status": "ok", "match_id": str(match.id)}
