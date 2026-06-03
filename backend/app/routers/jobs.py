from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str, db: AsyncSession = Depends(get_db)):
    # TODO: fetch job row, return live step + progress percentage
    return {
        "job_id": job_id,
        "status": "pending",
        "step": "planning",
        "progress": 0,
    }
