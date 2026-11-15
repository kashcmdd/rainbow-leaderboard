"""glicko2 ratings

Revision ID: 0bfca0f20560
Revises: 09afc3c76250
Create Date: 2026-11-15 00:50:40.234629

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0bfca0f20560'
down_revision: Union[str, Sequence[str], None] = '09afc3c76250'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("ratings", sa.Column("rd", sa.Float(), nullable=False, server_default="350"))
    op.add_column("ratings", sa.Column("volatility", sa.Float(), nullable=False, server_default="0.06"))
    op.add_column("ratings", sa.Column("rd_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE ratings SET rd_updated_at = COALESCE(last_active, now())")
    # Established players are not as uncertain as brand-new ones.
    op.execute(
        "UPDATE ratings SET rd = GREATEST(80, 350 - 9 * COALESCE(matches_played, 0))"
    )
    op.drop_column("ratings", "is_decaying")
    op.drop_column("ratings", "decay_applied")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("ratings", sa.Column("is_decaying", sa.Boolean(), nullable=True))
    op.add_column("ratings", sa.Column("decay_applied", sa.Integer(), nullable=False, server_default="0"))
    op.drop_column("ratings", "rd_updated_at")
    op.drop_column("ratings", "volatility")
    op.drop_column("ratings", "rd")
