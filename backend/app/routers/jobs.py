import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.models import Report, ResearchJob, User

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}/status")
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = await db.get(ResearchJob, uuid.UUID(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    response = {
        "job_id": job_id,
        "status": job.status,
        "completed_at": job.completed_at,
    }

    if job.status == "done":
        result = await db.execute(
            select(Report).where(Report.job_id == uuid.UUID(job_id))
        )
        report = result.scalar_one_or_none()
        response["report_id"] = str(report.id) if report else None

    return response
