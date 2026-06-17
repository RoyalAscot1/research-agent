"""unique constraint on reports.job_id

Revision ID: a1b2c3d4e5f6
Revises: 529d44410e2f
Create Date: 2026-06-17 19:00:00.000000

Enforces one report per research job at the DB level. The ORM already models
this as a one-to-one (`uselist=False`) and `jobs.py` queries reports-by-job with
`scalar_one_or_none()`, which raises `MultipleResultsFound` (→ 500) if a
retry/bug ever produces two rows for the same job. The constraint makes the
invariant authoritative.

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '529d44410e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_reports_job_id", "reports", ["job_id"])


def downgrade() -> None:
    op.drop_constraint("uq_reports_job_id", "reports", type_="unique")
