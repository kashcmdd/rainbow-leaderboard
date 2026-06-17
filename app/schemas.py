from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime


class PlayerCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)


class AvatarSet(BaseModel):
    url: str = Field(..., min_length=1, max_length=512)


class PlayerOut(BaseModel):
    id: UUID
    username: str
    discord_id: Optional[str] = None
    avatar_url: Optional[str] = None
    is_admin: bool = False
    is_banned: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class TeamCreate(BaseModel):
    format: str = Field(..., pattern=r"^\d+v\d+$")
    name: Optional[str] = None
    player_ids: list[UUID]


class MatchReport(BaseModel):
    format: str = Field(..., pattern=r"^\d+v\d+$")
    team_a_player_ids: list[UUID] = Field(default_factory=list)
    team_b_player_ids: list[UUID] = Field(default_factory=list)
    team_a_player_names: list[str] = Field(default_factory=list)
    team_b_player_names: list[str] = Field(default_factory=list)
    score_a: int = Field(..., ge=0)
    score_b: int = Field(..., ge=0)

    def infer_winner(self) -> Optional[str]:
        if self.score_a > self.score_b:
            return "a"
        elif self.score_b > self.score_a:
            return "b"
        return None


class RatingOut(BaseModel):
    format: str
    elo: int
    matches_played: int
    wins: int
    losses: int
    last_updated: datetime

    class Config:
        from_attributes = True


class PlayerWithRating(PlayerOut):
    ratings: list[RatingOut] = []


class LeaderboardEntry(BaseModel):
    rank: int
    player_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    name: str
    elo: int
    rank_title: str = "Unranked"
    rank_color: str = "#666666"
    matches_played: int
    wins: int
    losses: int
    win_rate: float
    streak: int = 0


class MatchOut(BaseModel):
    id: UUID
    format: str
    score_a: int
    score_b: int
    played_at: datetime
    team_a_name: str
    team_b_name: str
    winner_name: Optional[str] = None
    elo_delta_a: Optional[int] = None
    elo_delta_b: Optional[int] = None
