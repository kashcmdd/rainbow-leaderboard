from fastapi import Request, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.models import Player


def get_user(request: Request) -> dict | None:
    return request.session.get("user")


def require_user(request: Request) -> dict:
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    return user


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user = require_user(request)
    discord_id = user["id"]

    is_admin = discord_id in settings.admin_discord_ids
    if not is_admin:
        result = await db.execute(select(Player).where(Player.discord_id == discord_id))
        player = result.scalar_one_or_none()
        is_admin = player is not None and player.is_admin

    if not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin")

    if user.get("is_admin") != is_admin:
        user["is_admin"] = is_admin
        request.session["user"] = user

    return user
