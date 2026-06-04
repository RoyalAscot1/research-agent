import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.graph.graph import call_gemini_followup
from app.models.models import FollowUp, Report, ResearchJob, User

router = APIRouter(tags=["reports"])


class FollowUpRequest(BaseModel):
    question: str


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    report = await db.get(Report, rid)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    job = await db.get(ResearchJob, report.job_id)

    completed_in_seconds = None
    if job and job.completed_at and job.created_at:
        completed_in_seconds = round((job.completed_at - job.created_at).total_seconds())

    result = await db.execute(
        select(FollowUp)
        .where(FollowUp.report_id == report.id)
        .order_by(FollowUp.turn_number)
    )
    follow_ups = result.scalars().all()

    sources = [
        {
            "title": s.get("title", ""),
            "url": s.get("url", ""),
            "published_date": s.get("published_date"),
            "score": s.get("score"),
        }
        for s in (report.raw_context or [])
    ]

    return {
        "report_id": str(report.id),
        "job_id": str(report.job_id),
        "query": job.query if job else None,
        "report_markdown": report.report_markdown,
        "sentiment_positive": report.sentiment_positive,
        "sentiment_neutral": report.sentiment_neutral,
        "sentiment_negative": report.sentiment_negative,
        "youtube_comment_volume": report.youtube_comment_volume,
        "overall_sentiment": report.overall_sentiment,
        "source_count": report.source_count,
        "sources": sources,
        "created_at": report.created_at,
        "completed_in_seconds": completed_in_seconds,
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
    current_user: User = Depends(get_current_user),
):
    # Load and authorise
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    report = await db.get(Report, rid)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Enforce 5-follow-up cap
    count_result = await db.execute(
        select(func.count()).where(FollowUp.report_id == report.id)
    )
    existing_count = count_result.scalar_one()
    if existing_count >= 5:
        raise HTTPException(status_code=429, detail="Follow-up limit reached (5 max)")

    # Load job for original query
    job = await db.get(ResearchJob, report.job_id)
    query = job.query if job else ""

    # Load prior turns for conversation history
    prior_result = await db.execute(
        select(FollowUp)
        .where(FollowUp.report_id == report.id)
        .order_by(FollowUp.turn_number)
    )
    prior_turns = [
        {"turn_number": fu.turn_number, "question": fu.question, "answer": fu.answer}
        for fu in prior_result.scalars().all()
    ]

    # Call Gemini
    answer = await call_gemini_followup(
        query=query,
        report_markdown=report.report_markdown or "",
        sources=report.raw_context or [],
        prior_turns=prior_turns,
        question=body.question,
    )

    # Persist
    turn_number = existing_count + 1
    follow_up = FollowUp(
        id=uuid.uuid4(),
        report_id=report.id,
        user_id=current_user.id,
        question=body.question,
        answer=answer,
        turn_number=turn_number,
    )
    db.add(follow_up)
    await db.commit()

    return {"answer": answer, "turn_number": turn_number}
