"""Cross-meeting search endpoint."""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.v1.endpoints.auth import require_org_member
from app.models.user import User
from app.services.search_service import search_meetings

router = APIRouter()


@router.get("/", response_model=List[Dict[str, Any]])
async def search(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """
    Search across all meetings — titles, summaries, decisions, topics, and transcripts.
    Returns ranked results with highlighted snippets.
    """
    return search_meetings(
        db=db,
        query=q,
        organization_id=str(current_user.organization_id),
        user_id=str(current_user.id),
        limit=limit,
    )
