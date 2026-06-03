from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(tags=["history"])


@router.get("/history")
async def list_history(db: AsyncSession = Depends(get_db)):
    # TODO: return paginated reports list for the authenticated user
    return {"reports": []}


@router.delete("/history/{report_id}", status_code=204)
async def delete_report(report_id: str, db: AsyncSession = Depends(get_db)):
    # TODO: verify ownership, delete report and its follow_ups
    return None


@router.delete("/history", status_code=204)
async def clear_history(db: AsyncSession = Depends(get_db)):
    # TODO: delete all reports for the authenticated user
    return None
