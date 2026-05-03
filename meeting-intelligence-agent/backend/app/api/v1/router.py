"""
API V1 Router - Main router aggregating all endpoints
"""
from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    dashboard,
    organizations,
    notifications,
    meetings,
    transcripts,
    action_items,
    mentions,
    analytics,
    integrations,
    users,
    search,
    patterns,
    decisions,
)

api_router = APIRouter()

# Include all sub-routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(dashboard.router, tags=["Dashboard"])
api_router.include_router(organizations.router, prefix="/org", tags=["Organization"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(meetings.router, prefix="/meetings", tags=["Meetings"])
api_router.include_router(transcripts.router, prefix="/transcripts", tags=["Transcripts"])
api_router.include_router(action_items.router, prefix="/action-items", tags=["Action Items"])
api_router.include_router(mentions.router, prefix="/mentions", tags=["Mentions"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["Integrations"])
api_router.include_router(search.router, prefix="/search", tags=["Search"])
api_router.include_router(patterns.router, prefix="/patterns", tags=["Patterns"])
api_router.include_router(decisions.router, prefix="/decisions", tags=["Decisions"])
