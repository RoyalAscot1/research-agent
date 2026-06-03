import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.models import FollowUp, Report, ResearchJob, User

router = APIRouter(tags=["history"])


@router.get("/history")
async def list_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Report, ResearchJob.query, ResearchJob.created_at)
        .join(ResearchJob, Report.job_id == ResearchJob.id)
        .where(Report.user_id == current_user.id)
        .order_by(ResearchJob.created_at.desc())
    )
    rows = result.all()

    return {
        "reports": [
            {
                "report_id": str(report.id),
                "job_id": str(report.job_id),
                "query": query,
                "overall_sentiment": report.overall_sentiment,
                "created_at": job_created_at,
            }
            for report, query, job_created_at in rows
        ]
    }


@router.delete("/history/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = await db.get(Report, uuid.UUID(report_id))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    await db.execute(delete(FollowUp).where(FollowUp.report_id == report.id))
    await db.delete(report)
    await db.commit()


@router.delete("/history", status_code=204)
async def clear_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report_ids_result = await db.execute(
        select(Report.id).where(Report.user_id == current_user.id)
    )
    report_ids = [row[0] for row in report_ids_result.all()]

    if report_ids:
        await db.execute(delete(FollowUp).where(FollowUp.report_id.in_(report_ids)))
        await db.execute(delete(Report).where(Report.id.in_(report_ids)))
        await db.commit()
