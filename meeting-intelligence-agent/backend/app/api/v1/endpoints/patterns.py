"""Recurring pattern detection endpoints."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_org_member
from app.core.database import get_db
from app.models.user import User
from app.services.pattern_detection import detect_patterns

router = APIRouter()


@router.get("/", response_model=Dict[str, Any])
async def get_patterns(
    lookback_days: int = Query(60, ge=7, le=365),
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """
    Detect recurring patterns across meetings:
    - Silent attendees (invited but never speak across 3+ meetings)
    - Chronic overdue action items (owners with 3+ overdue items)
    - Unresolved recurring topics (same topic in 3+ meetings, no decision)
    - Blocked items stuck for 7+ days
    """
    return detect_patterns(db, str(current_user.organization_id), lookback_days=lookback_days)
