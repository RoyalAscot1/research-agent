from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(tags=["queries"])


class QueryRequest(BaseModel):
    query: str


@router.post("/queries", status_code=202)
async def create_query(body: QueryRequest, db: AsyncSession = Depends(get_db)):
    # TODO: create research_job row, enqueue LangGraph agent
    return {"job_id": "stub-job-id", "status": "pending"}
