"""rename_reddit_volume_to_youtube_comment_volume

Revision ID: 529d44410e2f
Revises: 707c4d06cff5
Create Date: 2026-06-03 21:32:35.927210

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '529d44410e2f'
down_revision: Union[str, None] = '707c4d06cff5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("reports", "reddit_volume", new_column_name="youtube_comment_volume")


def downgrade() -> None:
    op.alter_column("reports", "youtube_comment_volume", new_column_name="reddit_volume")
