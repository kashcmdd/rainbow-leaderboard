import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, Query
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_admin
from app.models import Player, Rating, RatingHistory, Match, Team, TeamMember, Season, SeasonSnapshot
from app.schemas import PlayerCreate, PlayerOut, AvatarSet
from app.schemas_admin import PlayerUpdate
from app.ranks import get_rank

AVATAR_DIR = Path(__file__).parent.parent / "static" / "avatars"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB

router = APIRouter(prefix="/api/players", tags=["players"])


@router.post("", response_model=PlayerOut)
async def create_player(body: PlayerCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Player).where(Player.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Player already exists")
    player = Player(username=body.username)
    db.add(player)
    await db.flush()
    rating = Rating(player_id=player.id, format="1v1", elo=0, wins=0, losses=0, matches_played=0)
    db.add(rating)
    await db.commit()
    await db.refresh(player)
    return player


@router.get("", response_model=list[PlayerOut])
async def list_players(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Player).order_by(Player.username))
    return result.scalars().all()


@router.get("/{player_id}")
async def get_player(player_id: str, db: AsyncSession = Depends(get_db)):
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
async def update_player(player_id: str, body: PlayerUpdate, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
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
        if body.wins is not None:
            rating.wins = body.wins
        if body.losses is not None:
            rating.losses = body.losses
        rating.matches_played = rating.wins + rating.losses

    await db.commit()
    return {"status": "ok"}


@router.post("/{player_id}/avatar")
async def set_avatar(
    player_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(None),
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
            raise HTTPException(status_code=400, detail="Send a file (multipart 'file') or JSON with {'url': '...'}")
        url = body.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="Provide a 'url' field in JSON body or upload a 'file'")
        player.avatar_url = url

    await db.commit()
    return {"status": "ok", "avatar_url": player.avatar_url}


@router.delete("/{player_id}/avatar")
async def remove_avatar(player_id: str, request: Request, db: AsyncSession = Depends(get_db)):
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
async def delete_player(player_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    await db.delete(player)
    await db.commit()
    return {"status": "deleted"}


@router.get("/{player_id}/history")
async def get_player_history(
    player_id: str,
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

    out = []
    for e in reversed(entries):
        match = await db.get(Match, e.match_id)
        if not match:
            continue

        def get_team_name(team_id):
            team = match.team_a if team_id == match.team_a_id else match.team_b
            return " + ".join(
                mem.player.username for mem in (team.members if team else [])
            ) if team else "Unknown"

        # Determine opponent
        opponent = None
        result_label = None
        if match.team_a_id:
            for team_id in [match.team_a_id, match.team_b_id]:
                team = match.team_a if team_id == match.team_a_id else match.team_b
                if team:
                    member_ids = [str(m.player_id) for m in team.members]
                    if player_id in member_ids:
                        other_team = match.team_b if team_id == match.team_a_id else match.team_a
                        if other_team:
                            opponent = " + ".join(m.player.username for m in other_team.members)
                        if match.winner_team_id == team_id:
                            result_label = "win"
                        else:
                            result_label = "loss"

        out.append({
            "match_id": str(e.match_id),
            "opponent": opponent,
            "result": result_label,
            "elo_before": e.elo_before,
            "elo_after": e.elo_after,
            "delta": e.elo_after - e.elo_before,
            "score_a": match.score_a if match else 0,
            "score_b": match.score_b if match else 0,
            "format": match.format if match else format,
            "timestamp": e.changed_at.isoformat(),
        })
    return out


@router.get("/{player_id}/seasons")
async def get_player_seasons(player_id: str, db: AsyncSession = Depends(get_db)):
    player = await db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    snapshots = (await db.execute(
        select(SeasonSnapshot)
        .where(SeasonSnapshot.player_id == player_id)
        .order_by(SeasonSnapshot.id.desc())
        .limit(50)
    )).scalars().all()

    out = []
    for s in snapshots:
        season = await db.get(Season, s.season_id)
        out.append({
            "season_id": str(s.season_id),
            "season_name": season.name if season else "Unknown",
            "season_status": season.status if season else "archived",
            "season_end": season.end_date.isoformat() if season and season.end_date else None,
            "elo": s.elo,
            "rank_title": s.rank_title,
            "rank_color": s.rank_color,
            "wins": s.wins,
            "losses": s.losses,
            "position": s.position,
        })
    return out
