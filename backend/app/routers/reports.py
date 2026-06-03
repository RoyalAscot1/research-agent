from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(tags=["reports"])


class FollowUpRequest(BaseModel):
    question: str


@router.get("/reports/{report_id}")
async def get_report(report_id: str, db: AsyncSession = Depends(get_db)):
    # TODO: fetch report + follow_ups from DB, verify ownership
    return {"report_id": report_id}


@router.post("/reports/{report_id}/followup")
async def create_followup(
    report_id: str,
    body: FollowUpRequest,
    db: AsyncSession = Depends(get_db),
):
    # TODO: enforce 5-followup cap, load raw_context, call synthesizer node
    return {"answer": "stub answer", "turn_number": 1}
