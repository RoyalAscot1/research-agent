import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.models import ResearchJob, User

router = APIRouter(tags=["queries"])


class QueryRequest(BaseModel):
    query: str


@router.post("/queries", status_code=202)
async def create_query(
    body: QueryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = ResearchJob(
        id=uuid.uuid4(),
        user_id=current_user.id,
        query=body.query,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    # TODO: enqueue LangGraph agent (step 4)
    return {"job_id": str(job.id), "status": job.status}
