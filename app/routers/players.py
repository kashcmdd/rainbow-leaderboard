import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.csrf import csrf_protect
from app.database import get_db
from app.deps import require_admin, require_user
from app.models import Match, Player, Rating, RatingHistory, Season, SeasonSnapshot, Team, TeamMember
from app.ranks import get_rank
from app.schemas import AvatarSet, PlayerCreate, PlayerOut
from app.schemas_admin import PlayerUpdate

AVATAR_DIR = Path(__file__).parent.parent / "static" / "avatars"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB

router = APIRouter(prefix="/api/players", tags=["players"])


@router.post("", response_model=PlayerOut)
async def create_player(body: PlayerCreate, db: AsyncSession = Depends(get_db), _: dict = Depends(require_user), __: None = Depends(csrf_protect)):
    existing = await db.execute(select(Player).where(Player.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Player already exists")
    player = Player(username=body.username)
    db.add(player)
    await db.flush()
    rating = Rating(player_id=player.id, format="1v1", elo=settings.starting_elo, wins=0, losses=0, matches_played=0)
    db.add(rating)
    await db.commit()
    await db.refresh(player)
    return player


@router.get("", response_model=list[PlayerOut])
async def list_players(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Player).order_by(Player.username))
    return result.scalars().all()


@router.get("/compare")
async def compare_players(
    player1: str = Query(...),
    player2: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    p1_id = uuid.UUID(player1)
    p2_id = uuid.UUID(player2)

    async def _player_data(player_id):
        result = await db.execute(
            select(Player)
            .options(selectinload(Player.ratings))
            .where(Player.id == player_id)
        )
        player = result.scalar_one_or_none()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        position_cache = {}
        for r in player.ratings:
            if r.format not in position_cache:
                count_higher = await db.execute(
                    select(func.count(Rating.id)).where(
                        Rating.format == r.format,
                        Rating.player_id.isnot(None),
                        Rating.elo > r.elo,
                    )
                )
                higher_count = count_higher.scalar() or 0
                position_cache[r.format] = higher_count + 1
        ratings_list = [
            {
                "format": r.format,
                "elo": r.elo,
                "rank_title": get_rank(r.elo, position_cache.get(r.format))[0],
                "rank_color": get_rank(r.elo, position_cache.get(r.format))[1],
                "matches_played": r.matches_played,
                "wins": r.wins,
                "losses": r.losses,
                "streak": r.streak or 0,
                "top_position": r.top_position,
                "is_decaying": r.is_decaying,
            }
            for r in player.ratings
        ]
        return {
            "id": str(player.id),
            "username": player.username,
            "avatar_url": player.avatar_url,
            "discord_id": player.discord_id,
            "created_at": player.created_at.isoformat(),
            "ratings": ratings_list,
        }

    p1_data = await _player_data(p1_id)
    p2_data = await _player_data(p2_id)

    p1_team_ids = (await db.execute(
        select(TeamMember.team_id).where(TeamMember.player_id == p1_id)
    )).scalars().all()

    p2_team_ids = (await db.execute(
        select(TeamMember.team_id).where(TeamMember.player_id == p2_id)
    )).scalars().all()

    h2h_matches = await db.execute(
        select(Match).options(
            selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
            selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
        ).where(
            ((Match.team_a_id.in_(p1_team_ids)) & (Match.team_b_id.in_(p2_team_ids))) |
            ((Match.team_a_id.in_(p2_team_ids)) & (Match.team_b_id.in_(p1_team_ids)))
        ).order_by(Match.played_at.desc())
    )
    h2h_matches = h2h_matches.scalars().all()

    p1_wins = 0
    p2_wins = 0
    match_list = []

    for m in h2h_matches:
        def team_name(team):
            return " + ".join(mem.player.username for mem in team.members) if team and team.members else "Unknown"
        a_name = team_name(m.team_a)
        b_name = team_name(m.team_b)
        p1_in_a = any(str(mem.player_id) == player1 for mem in m.team_a.members) if m.team_a else False
        p1_in_b = any(str(mem.player_id) == player1 for mem in m.team_b.members) if m.team_b else False
        opponent = b_name if p1_in_a else a_name
        p1_won = None
        if m.winner_team_id:
            if p1_in_a and m.winner_team_id == m.team_a_id:
                p1_won = True
            elif p1_in_b and m.winner_team_id == m.team_b_id:
                p1_won = True
            elif p1_in_a or p1_in_b:
                p1_won = False
        if p1_won is True:
            p1_wins += 1
        elif p1_won is False:
            p2_wins += 1
        match_list.append({
            "match_id": str(m.id),
            "opponent": opponent,
            "result": "win" if p1_won else "loss",
            "score_a": m.score_a,
            "score_b": m.score_b,
            "format": m.format,
            "timestamp": m.played_at.isoformat(),
        })

    return {
        "player1": p1_data,
        "player2": p2_data,
        "head_to_head": {
            "player1_wins": p1_wins,
            "player2_wins": p2_wins,
            "total_matches": len(match_list),
            "matches": match_list,
        },
    }


@router.get("/{player_id}")
async def get_player(player_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Player)
        .options(selectinload(Player.ratings))
        .where(Player.id == player_id)
    )
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Compute leaderboard position for each format
    position_cache = {}
    for r in player.ratings:
        if r.format not in position_cache:
            count_higher = await db.execute(
                select(func.count(Rating.id)).where(
                    Rating.format == r.format,
                    Rating.player_id.isnot(None),
                    Rating.elo > r.elo,
                )
            )
            higher_count = count_higher.scalar() or 0
            position_cache[r.format] = higher_count + 1

    ratings_list = [
        {
            "format": r.format,
            "elo": r.elo,
            "rank_title": get_rank(r.elo, position_cache.get(r.format))[0],
            "rank_color": get_rank(r.elo, position_cache.get(r.format))[1],
            "matches_played": r.matches_played,
            "wins": r.wins,
            "losses": r.losses,
            "streak": r.streak or 0,
            "top_position": r.top_position,
            "is_decaying": r.is_decaying,
        }
        for r in player.ratings
    ]
    return {
        "id": str(player.id),
        "username": player.username,
        "avatar_url": player.avatar_url,
        "discord_id": player.discord_id,
        "created_at": player.created_at.isoformat(),
        "ratings": ratings_list,
    }


@router.patch("/{player_id}")
async def update_player(player_id: uuid.UUID, body: PlayerUpdate, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin), __: None = Depends(csrf_protect)):
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if body.username is not None:
        player.username = body.username

    if body.discord_id is not None:
        player.discord_id = body.discord_id

    if any(v is not None for v in [body.elo, body.wins, body.losses]):
        result = await db.execute(
            select(Rating).where(Rating.player_id == player_id, Rating.format == "1v1")
        )
        rating = result.scalar_one_or_none()
        if not rating:
            rating = Rating(player_id=player_id, format="1v1")
            db.add(rating)
        if body.elo is not None:
            rating.elo = body.elo
            rating.decay_applied = 0
            rating.is_decaying = False
        if body.wins is not None:
            rating.wins = body.wins
        if body.losses is not None:
            rating.losses = body.losses
        rating.matches_played = rating.wins + rating.losses

    await db.commit()
    return {"status": "ok"}


@router.post("/{player_id}/avatar")
async def set_avatar(
    player_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    body: AvatarSet = None,
    file: UploadFile = File(None),
    _: None = Depends(csrf_protect),
):
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Auth: player can only update their own, admins can update anyone's
    session_user = request.session.get("user")
    is_admin = session_user and session_user.get("is_admin")
    is_owner = session_user and (
        str(player.discord_id) == str(session_user["id"]) if player.discord_id else False
    )
    if not is_admin and not is_owner:
        raise HTTPException(status_code=403, detail="Not authorized")

    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large (max 2MB)")
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{player_id}_{uuid.uuid4().hex[:8]}{ext}"
        filepath = AVATAR_DIR / filename
        filepath.write_bytes(contents)
        avatar_url = str(request.base_url) + f"static/avatars/{filename}"
        player.avatar_url = avatar_url
    else:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a file (multipart 'file') or JSON with {'url': '...'}") from None
        url = body.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="Provide a 'url' field in JSON body or upload a 'file'")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Invalid URL protocol")
        parsed = __import__("urllib.parse").urlparse(url)
        hostname = parsed.hostname or ""
        blocked = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::ffff:0:0", "0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0", "db", "metadata.google.internal", "metadata.internal"}
        if hostname in blocked or hostname.startswith("169.254.") or hostname.startswith("10.") or hostname.startswith("172.") or hostname.startswith("192.168."):
            raise HTTPException(status_code=400, detail="URL cannot point to internal services")
        player.avatar_url = url

    await db.commit()
    return {"status": "ok", "avatar_url": player.avatar_url}


@router.delete("/{player_id}/avatar")
async def remove_avatar(player_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db), _: None = Depends(csrf_protect)):
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    session_user = request.session.get("user")
    is_admin = session_user and session_user.get("is_admin")
    is_owner = session_user and (
        str(player.discord_id) == str(session_user["id"]) if player.discord_id else False
    )
    if not is_admin and not is_owner:
        raise HTTPException(status_code=403, detail="Not authorized")
    player.avatar_url = None
    await db.commit()
    return {"status": "ok"}


@router.delete("/{player_id}")
async def delete_player(player_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin), __: None = Depends(csrf_protect)):
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    await db.delete(player)
    await db.commit()
    return {"status": "deleted"}


@router.get("/{player_id}/history")
async def get_player_history(
    player_id: uuid.UUID,
    format: str = Query("1v1"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    # Get the player's rating
    rating = (await db.execute(
        select(Rating).where(Rating.player_id == player_id, Rating.format == format)
    )).scalar_one_or_none()
    if not rating:
        return []

    entries = (await db.execute(
        select(RatingHistory)
        .where(RatingHistory.rating_id == rating.id)
        .order_by(desc(RatingHistory.changed_at))
        .limit(limit)
    )).scalars().all()

    # One query for every match in the window instead of one per history row.
    match_ids = {e.match_id for e in entries if e.match_id}
    match_map = {}
    if match_ids:
        matches = (await db.execute(
            select(Match)
            .options(
                selectinload(Match.team_a).selectinload(Team.members).selectinload(TeamMember.player),
                selectinload(Match.team_b).selectinload(Team.members).selectinload(TeamMember.player),
            )
            .where(Match.id.in_(match_ids))
        )).scalars().all()
        match_map = {m.id: m for m in matches}

    out = []
    for e in reversed(entries):
        if e.match_id is None:
            # Decay entries have no match attached.
            out.append({
                "match_id": None,
                "opponent": None,
                "result": None,
                "elo_before": e.elo_before,
                "elo_after": e.elo_after,
                "delta": e.elo_after - e.elo_before,
                "score_a": None,
                "score_b": None,
                "format": "decay",
                "timestamp": e.changed_at.isoformat(),
            })
            continue

        match = match_map.get(e.match_id)
        if not match:
            continue

        def team_name(team):
            return " + ".join(m.player.username for m in team.members) if team and team.members else "Unknown"

        a_name = team_name(match.team_a)
        b_name = team_name(match.team_b)

        # Determine opponent and result for this player
        opponent = None
        result_label = None
        if match.team_a_id and match.team_b_id:
            member_ids_a = {str(m.player_id) for m in match.team_a.members}
            member_ids_b = {str(m.player_id) for m in match.team_b.members}
            if str(player_id) in member_ids_a:
                opponent = b_name
                result_label = "win" if match.winner_team_id == match.team_a_id else "loss"
            elif str(player_id) in member_ids_b:
                opponent = a_name
                result_label = "win" if match.winner_team_id == match.team_b_id else "loss"

        out.append({
            "match_id": str(e.match_id),
            "opponent": opponent,
            "result": result_label,
            "elo_before": e.elo_before,
            "elo_after": e.elo_after,
            "delta": e.elo_after - e.elo_before,
            "score_a": match.score_a,
            "score_b": match.score_b,
            "format": match.format,
            "timestamp": e.changed_at.isoformat(),
        })
    return out


@router.get("/{player_id}/seasons")
async def get_player_seasons(player_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    player = await db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    snapshots = (await db.execute(
        select(SeasonSnapshot, Season)
        .join(Season, Season.id == SeasonSnapshot.season_id)
        .where(SeasonSnapshot.player_id == player_id)
        .order_by(Season.end_date.desc().nullslast(), Season.start_date.desc())
        .limit(50)
    )).all()

    out = []
    for snapshot, season in snapshots:
        out.append({
            "season_id": str(snapshot.season_id),
            "season_name": season.name,
            "season_status": season.status,
            "season_end": season.end_date.isoformat() if season.end_date else None,
            "elo": snapshot.elo,
            "rank_title": snapshot.rank_title,
            "rank_color": snapshot.rank_color,
            "wins": snapshot.wins,
            "losses": snapshot.losses,
            "position": snapshot.position,
        })
    return out
