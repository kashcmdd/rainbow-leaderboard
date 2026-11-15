"""track rating decay

Revision ID: 09afc3c76250
Revises: 69eebd15b9b0
Create Date: 2026-11-15 00:22:25.378647

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '09afc3c76250'
down_revision: Union[str, Sequence[str], None] = '69eebd15b9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ratings",
        sa.Column("decay_applied", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column(
        "rating_history",
        "match_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM rating_history WHERE match_id IS NULL")
    op.alter_column(
        "rating_history",
        "match_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.drop_column("ratings", "decay_applied")
