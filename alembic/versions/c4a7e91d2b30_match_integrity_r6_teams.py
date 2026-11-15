"""match integrity, R6 metadata, and managed teams

Revision ID: c4a7e91d2b30
Revises: 0bfca0f20560
Create Date: 2026-11-15 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4a7e91d2b30'
down_revision: Union[str, Sequence[str], None] = '0bfca0f20560'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("matches", sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"))
    op.add_column("matches", sa.Column("reported_by", sa.String(32), nullable=True))
    op.add_column("matches", sa.Column("reported_by_name", sa.String(64), nullable=True))
    op.add_column("matches", sa.Column("confirmed_by", sa.String(32), nullable=True))
    op.add_column("matches", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("matches", sa.Column("dispute_reason", sa.Text(), nullable=True))
    op.add_column("matches", sa.Column("evidence_url", sa.String(512), nullable=True))
    op.add_column("matches", sa.Column("map_name", sa.String(32), nullable=True))
    op.add_column("matches", sa.Column("side_a", sa.String(16), nullable=True))
    op.add_column("matches", sa.Column("operator_bans", sa.Text(), nullable=True))
    op.create_index("ix_matches_status", "matches", ["status"])

    op.add_column("teams", sa.Column("captain_id", sa.UUID(as_uuid=True), nullable=True))
    op.add_column("teams", sa.Column("created_by", sa.String(32), nullable=True))
    op.add_column("teams", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_foreign_key(
        "fk_teams_captain_id_players", "teams", "players", ["captain_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_teams_captain_id_players", "teams", type_="foreignkey")
    op.drop_column("teams", "is_active")
    op.drop_column("teams", "created_by")
    op.drop_column("teams", "captain_id")

    op.drop_index("ix_matches_status", table_name="matches")
    op.drop_column("matches", "operator_bans")
    op.drop_column("matches", "side_a")
    op.drop_column("matches", "map_name")
    op.drop_column("matches", "evidence_url")
    op.drop_column("matches", "dispute_reason")
    op.drop_column("matches", "confirmed_at")
    op.drop_column("matches", "confirmed_by")
    op.drop_column("matches", "reported_by_name")
    op.drop_column("matches", "reported_by")
    op.drop_column("matches", "status")
