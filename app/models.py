import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Player(Base):
    __tablename__ = "players"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(64), unique=True, nullable=False, index=True)
    discord_id = Column(String(32), nullable=True, index=True)
    avatar_url = Column(String(512), nullable=True)
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    ratings = relationship("Rating", back_populates="player", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    format = Column(String(8), nullable=False)
    name = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    ratings = relationship("Rating", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)

    team = relationship("Team", back_populates="members")
    player = relationship("Player")


class Match(Base):
    __tablename__ = "matches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    format = Column(String(8), nullable=False)
    tournament_id = Column(String(64), nullable=True)
    bracket_round = Column(Integer, nullable=True)
    bracket_position = Column(Integer, nullable=True)
    team_a_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    team_b_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    score_a = Column(Integer, nullable=False)
    score_b = Column(Integer, nullable=False)
    winner_team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    elo_delta_a = Column(Integer, nullable=True)
    elo_delta_b = Column(Integer, nullable=True)
    played_at = Column(DateTime(timezone=True), default=utcnow)

    team_a = relationship("Team", foreign_keys=[team_a_id])
    team_b = relationship("Team", foreign_keys=[team_b_id])


class Rating(Base):
    __tablename__ = "ratings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True)
    format = Column(String(8), nullable=False)
    elo = Column(Integer, default=0, nullable=False)
    matches_played = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    top_position = Column(Integer, nullable=True)
    streak = Column(Integer, default=0)
    is_decaying = Column(Boolean, default=False)
    last_active = Column(DateTime(timezone=True), nullable=True)
    last_updated = Column(DateTime(timezone=True), default=utcnow)

    player = relationship("Player", back_populates="ratings")
    team = relationship("Team", back_populates="ratings")

    __table_args__ = (
        UniqueConstraint("player_id", "format", name="uq_player_format"),
        UniqueConstraint("team_id", "format", name="uq_team_format"),
    )


class RatingHistory(Base):
    __tablename__ = "rating_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rating_id = Column(UUID(as_uuid=True), ForeignKey("ratings.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    elo_before = Column(Integer, nullable=False)
    elo_after = Column(Integer, nullable=False)
    match_id = Column(UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False)
    changed_at = Column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(String(32), nullable=False)
    admin_name = Column(String(64), nullable=False)
    action = Column(String(256), nullable=False)
    target_id = Column(String(64), nullable=True)
    target_name = Column(String(64), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Tournament(Base):
    __tablename__ = "tournaments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False)
    status = Column(String(16), default="pending")  # pending, active, completed
    format = Column(String(8), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    participants = relationship("TournamentParticipant", back_populates="tournament", cascade="all, delete-orphan")


class TournamentParticipant(Base):
    __tablename__ = "tournament_participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tournament_id = Column(UUID(as_uuid=True), ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    seed = Column(Integer, nullable=True)
    placement = Column(Integer, nullable=True)

    tournament = relationship("Tournament", back_populates="participants")
    player = relationship("Player")


class Season(Base):
    __tablename__ = "seasons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False)
    start_date = Column(DateTime(timezone=True), default=utcnow)
    end_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), default="active")  # active, archived


class SeasonSnapshot(Base):
    __tablename__ = "season_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    season_id = Column(UUID(as_uuid=True), ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    player_name = Column(String(64), nullable=False)
    elo = Column(Integer, nullable=False)
    rank_title = Column(String(32), nullable=False)
    rank_color = Column(String(16), nullable=False)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    position = Column(Integer, nullable=True)

    season = relationship("Season")
    player = relationship("Player")
