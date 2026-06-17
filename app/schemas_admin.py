from pydantic import BaseModel, Field
from typing import Optional


class PlayerUpdate(BaseModel):
    username: Optional[str] = None
    elo: Optional[int] = Field(None, ge=0)
    wins: Optional[int] = Field(None, ge=0)
    losses: Optional[int] = Field(None, ge=0)
    discord_id: Optional[str] = None
