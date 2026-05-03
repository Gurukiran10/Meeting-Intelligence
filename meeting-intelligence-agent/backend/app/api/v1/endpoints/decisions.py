"""Decision context linking and institutional memory endpoints."""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_org_member
from app.core.database import get_db
from app.models.user import User
from app.services.decision_context_linking import get_decision_graph, search_institutional_memory

router = APIRouter()


@router.get("/graph", response_model=Dict[str, Any])
async def decision_graph(
    days: int = Query(90, ge=7, le=365),
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """
    Cross-meeting decision timeline grouped into evolution threads.
    Shows decisions that were revisited, reversed, or built upon across meetings.
    """
    return get_decision_graph(db, str(current_user.organization_id), days=days)


@router.get("/memory", response_model=List[Dict[str, Any]])
async def institutional_memory(
    q: str = Query(..., min_length=2),
    days: int = Query(365, ge=30, le=730),
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """
    Search all past decisions for institutional memory.
    Use this to answer 'did we decide this before?' or 'what was the outcome of X?'
    """
    return search_institutional_memory(db, str(current_user.organization_id), query=q, days=days)
