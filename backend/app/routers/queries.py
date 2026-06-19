import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.graph.graph import run_graph
from app.logging_config import get_logger
from app.models.models import ResearchJob, User
from app.rate_limit import QUERIES_LIMIT, limiter

router = APIRouter(tags=["queries"])
log = get_logger(__name__)


class QueryRequest(BaseModel):
    query: str


@router.post("/queries", status_code=202)
@limiter.limit(QUERIES_LIMIT)
async def create_query(
    request: Request,
    body: QueryRequest,
    background_tasks: BackgroundTasks,
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
    log.info("job.created", job_id=str(job.id), user_id=str(current_user.id))
    background_tasks.add_task(run_graph, str(job.id), str(current_user.id), body.query)
    return {"job_id": str(job.id), "status": job.status}
