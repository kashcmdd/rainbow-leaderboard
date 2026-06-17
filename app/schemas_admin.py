from pydantic import BaseModel, Field
from typing import Optional


class PlayerUpdate(BaseModel):
    username: Optional[str] = None
    elo: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    discord_id: Optional[str] = None
