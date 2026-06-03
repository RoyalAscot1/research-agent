import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import ResearchJob

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(ResearchJob, uuid.UUID(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job.status,
        "completed_at": job.completed_at,
    }
