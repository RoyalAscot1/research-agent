"""unique constraint on follow_ups (report_id, turn_number)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-17 19:30:00.000000

Enforces one follow-up per (report, turn_number) at the DB level. The follow-up
endpoint's count -> cap-check -> slow Gemini call -> insert sequence isn't atomic,
so concurrent follow-ups for the same report can both pass the 5-cap and insert a
duplicate turn_number (which the frontend uses as a React key). The constraint
makes the database reject the second insert; the endpoint catches the resulting
IntegrityError and returns a clean 409.

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_follow_ups_report_turn", "follow_ups", ["report_id", "turn_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_follow_ups_report_turn", "follow_ups", type_="unique")
