import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.models import FollowUp, Report, User

router = APIRouter(tags=["reports"])


class FollowUpRequest(BaseModel):
    question: str


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = await db.get(Report, uuid.UUID(report_id))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await db.execute(
        select(FollowUp)
        .where(FollowUp.report_id == report.id)
        .order_by(FollowUp.turn_number)
    )
    follow_ups = result.scalars().all()

    return {
        "report_id": str(report.id),
        "job_id": str(report.job_id),
        "report_markdown": report.report_markdown,
        "sentiment_positive": report.sentiment_positive,
        "sentiment_neutral": report.sentiment_neutral,
        "sentiment_negative": report.sentiment_negative,
        "youtube_comment_volume": report.youtube_comment_volume,
        "overall_sentiment": report.overall_sentiment,
        "source_count": report.source_count,
        "suggested_followups": report.suggested_followups,
        "created_at": report.created_at,
        "follow_ups": [
            {
                "question": fu.question,
                "answer": fu.answer,
                "turn_number": fu.turn_number,
            }
            for fu in follow_ups
        ],
    }


@router.post("/reports/{report_id}/followup")
async def create_followup(
    report_id: str,
    body: FollowUpRequest,
    db: AsyncSession = Depends(get_db),
):
    # TODO: enforce 5-followup cap, load raw_context, call synthesizer node (step 11)
    return {"answer": "stub answer", "turn_number": 1}
