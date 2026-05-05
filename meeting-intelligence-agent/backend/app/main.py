"""
Meeting Intelligence Agent - Main Application Entry Point
"""
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sqlalchemy import text

from app.core.config import settings
from app.core.database import init_db, close_db
from app.core.redis import init_redis, close_redis, redis_client
from app.core.database import SessionLocal
from app.api.v1.router import api_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.core.logging import setup_logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Initialize Sentry for error tracking
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        environment=settings.APP_ENV,
        traces_sample_rate=1.0 if settings.APP_ENV == "development" else 0.1,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan events"""
    auto_sync_task: Optional[asyncio.Task] = None
    retention_task: Optional[asyncio.Task] = None
    auto_join_task: Optional[asyncio.Task] = None
    zoom_auto_join_task: Optional[asyncio.Task] = None

    async def _auto_sync_loop():
        from app.api.v1.endpoints.integrations import run_integration_auto_sync_for_all_users

        interval_seconds = max(settings.INTEGRATION_AUTO_SYNC_INTERVAL_MINUTES, 5) * 60
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                result = await asyncio.wait_for(run_integration_auto_sync_for_all_users(), timeout=180)
                logger.info(f"Integration auto-sync completed: {result}")
            except asyncio.TimeoutError:
                logger.error("Integration auto-sync timed out after 180 seconds")
            except Exception as exc:
                logger.error(f"Integration auto-sync failed: {exc}", exc_info=True)

    async def _retention_loop():
        from app.api.v1.endpoints.integrations import run_retention_enforcement_for_all_users

        interval_seconds = max(settings.RETENTION_ENFORCEMENT_INTERVAL_MINUTES, 60) * 60
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                result = await asyncio.wait_for(run_retention_enforcement_for_all_users(), timeout=120)
                logger.info(f"Retention enforcement completed: {result}")
            except asyncio.TimeoutError:
                logger.error("Retention enforcement timed out after 120 seconds")
            except Exception as exc:
                logger.error(f"Retention enforcement failed: {exc}", exc_info=True)

    async def _auto_join_loop():
        """
        DB-based auto-join: every MEET_BOT_LEAD_TIME_MINUTES, scan the meetings
        table for upcoming sessions and dispatch the bot.

        Uses Redis dedup locks — safe to run alongside the Celery Beat scheduler.
        Whichever fires first acquires the lock; the other skips silently.
        Falls back to in-process asyncio when Celery is unavailable (dev mode).
        """
        from app.core.database import SessionLocal
        from app.models.meeting import Meeting as MeetingModel
        from app.models.user import User as UserModel
        from app.services.bot_state import acquire_lock, get_bot_state, BotStatus
        from sqlalchemy import select
        from datetime import timedelta

        interval_seconds = max(settings.MEET_BOT_LEAD_TIME_MINUTES, 1) * 60

        while True:
            await asyncio.sleep(interval_seconds)
            if not settings.MEET_BOT_AUTO_JOIN_ENABLED:
                continue
            try:
                now = asyncio.get_event_loop().time()
                from datetime import datetime, timezone
                now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                window_start = now_dt - timedelta(minutes=settings.MEET_BOT_LEAD_TIME_MINUTES)
                window_end   = now_dt + timedelta(minutes=settings.MEET_BOT_LEAD_TIME_MINUTES + 1)

                with SessionLocal() as db:
                    upcoming = db.execute(
                        select(MeetingModel).where(
                            MeetingModel.deleted_at.is_(None),
                            MeetingModel.status.in_(["scheduled", "waiting_for_host"]),
                            MeetingModel.meeting_url.isnot(None),
                            MeetingModel.scheduled_start >= window_start,
                            MeetingModel.scheduled_start <= window_end,
                        )
                    ).scalars().all()

                for meeting in upcoming:
                    mid      = str(meeting.id)
                    platform = str(meeting.platform or "google_meet")
                    url      = str(meeting.meeting_url)
                    uid      = str(meeting.organizer_id)
                    org_id   = str(meeting.organization_id or "")

                    # Skip if bot already active (Redis or in-memory state)
                    state = await get_bot_state(mid)
                    if state and state.get("status") not in (
                        None, "", BotStatus.PENDING, BotStatus.COMPLETED,
                        BotStatus.FAILED, BotStatus.HOST_ABSENT,
                        BotStatus.BOT_REJECTED, BotStatus.WAITING_FOR_HOST,
                    ):
                        logger.debug("[AutoJoin] bot already active  meeting=%s  status=%s",
                                     mid, state.get("status"))
                        continue

                    # Try Celery first; fall back to in-process asyncio
                    try:
                        from app.tasks.auto_join import run_bot_session_task
                        run_bot_session_task.apply_async(
                            kwargs={
                                "meeting_id":          mid,
                                "meet_url":            url,
                                "user_id":             uid,
                                "organization_id":     org_id,
                                "platform":            platform,
                                "bot_display_name":    settings.MEET_BOT_DISPLAY_NAME,
                                "stay_duration_seconds": settings.MEET_BOT_STAY_DURATION_SECONDS,
                                "recordings_dir":      settings.RECORDINGS_DIR,
                                "attempt":             1,
                            },
                            queue="bots",
                        )
                        logger.info("[AutoJoin] enqueued Celery bot  meeting=%s  platform=%s", mid, platform)
                        continue
                    except Exception as celery_err:
                        logger.debug("[AutoJoin] Celery unavailable (%s) — using in-process bot", celery_err)

                    # In-process fallback (dev / no Celery)
                    if not await acquire_lock(mid):
                        logger.debug("[AutoJoin] lock held  meeting=%s", mid)
                        continue

                    if platform == "zoom":
                        from app.services.zoom_bot import join_zoom_meeting
                        asyncio.create_task(join_zoom_meeting(
                            zoom_url=url, user_id=uid, organization_id=org_id,
                            meeting_id=mid,
                            bot_display_name=settings.MEET_BOT_DISPLAY_NAME,
                            stay_duration_seconds=settings.MEET_BOT_STAY_DURATION_SECONDS,
                            recordings_dir=settings.RECORDINGS_DIR,
                        ), name=f"zoom_bot:{mid}")
                    else:
                        from app.services.meet_bot import join_google_meet
                        asyncio.create_task(join_google_meet(
                            meet_url=url, user_id=uid, organization_id=org_id,
                            meeting_id=mid,
                            bot_display_name=settings.MEET_BOT_DISPLAY_NAME,
                            stay_duration_seconds=settings.MEET_BOT_STAY_DURATION_SECONDS,
                            recordings_dir=settings.RECORDINGS_DIR,
                        ), name=f"meet_bot:{mid}")
                    logger.info("[AutoJoin] dispatched in-process bot  meeting=%s  platform=%s", mid, platform)

            except Exception as exc:
                logger.error(f"[AutoJoin] scheduler loop error: {exc}", exc_info=True)

    async def _zoom_auto_join_loop():
        """
        Every MEET_BOT_LEAD_TIME_MINUTES minutes, look for upcoming platform='zoom'
        meetings in the DB and dispatch the Zoom bot to join them.
        """
        from app.services.zoom_bot import join_zoom_meeting
        from app.models.meeting import Meeting as MeetingModel
        from app.models.user import User as UserModel
        from sqlalchemy import select
        from datetime import timedelta

        interval_seconds = max(settings.MEET_BOT_LEAD_TIME_MINUTES, 1) * 60

        while True:
            await asyncio.sleep(interval_seconds)
            if not settings.MEET_BOT_AUTO_JOIN_ENABLED:
                continue
            try:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                window_start = now - timedelta(minutes=settings.MEET_BOT_LEAD_TIME_MINUTES)
                window_end = now + timedelta(minutes=settings.MEET_BOT_LEAD_TIME_MINUTES + 1)

                with SessionLocal() as db:
                    upcoming = db.execute(
                        select(MeetingModel).where(
                            MeetingModel.platform == "zoom",
                            MeetingModel.deleted_at.is_(None),
                            MeetingModel.status.in_(["scheduled", "pending"]),
                            MeetingModel.scheduled_start >= window_start,
                            MeetingModel.scheduled_start <= window_end,
                            MeetingModel.meeting_url.isnot(None),
                        )
                    ).scalars().all()

                for meeting in upcoming:
                    try:
                        meet_url = str(meeting.meeting_url or "").strip()
                        if not meet_url:
                            continue
                        logger.info(
                            f"[ZoomAutoJoin] meeting={meeting.id} title={meeting.title!r} url={meet_url}"
                        )
                        await join_zoom_meeting(
                            zoom_url=meet_url,
                            user_id=str(meeting.organizer_id),
                            organization_id=str(meeting.organization_id or ""),
                            meeting_id=str(meeting.id),
                            bot_display_name=settings.MEET_BOT_DISPLAY_NAME,
                            stay_duration_seconds=settings.MEET_BOT_STAY_DURATION_SECONDS,
                            recordings_dir=settings.RECORDINGS_DIR,
                        )
                    except Exception as zm_exc:
                        logger.debug(f"[ZoomAutoJoin] skipped meeting={meeting.id}: {zm_exc}")
            except Exception as exc:
                logger.error(f"[ZoomAutoJoin] loop error: {exc}", exc_info=True)

    # Startup
    logger.info("Starting Meeting Intelligence Agent...")
    init_db()
    # Note: Redis is optional, will continue without it if not available
    try:
        await init_redis()
    except Exception as e:
        logger.warning(f"Redis initialization failed: {e}. Continuing without Redis.")

    if settings.ENABLE_INTEGRATION_AUTO_SYNC:
        auto_sync_task = asyncio.create_task(_auto_sync_loop())
        logger.info(
            "Integration auto-sync scheduler enabled "
            f"(interval={settings.INTEGRATION_AUTO_SYNC_INTERVAL_MINUTES} minutes)"
        )

    if settings.ENABLE_RETENTION_ENFORCEMENT_JOB:
        retention_task = asyncio.create_task(_retention_loop())
        logger.info(
            "Retention enforcement scheduler enabled "
            f"(interval={settings.RETENTION_ENFORCEMENT_INTERVAL_MINUTES} minutes)"
        )

    # Bot dispatch is handled exclusively by Celery Beat (poll_db_and_auto_join task).
    # The asyncio loops below are disabled to prevent duplicate dispatch.
    # if settings.MEET_BOT_AUTO_JOIN_ENABLED:
    #     auto_join_task = asyncio.create_task(_auto_join_loop())
    #     zoom_auto_join_task = asyncio.create_task(_zoom_auto_join_loop())
    #     logger.info(
    #         f"[AutoJoin] Google Meet + Zoom schedulers enabled "
    #         f"(check every {settings.MEET_BOT_LEAD_TIME_MINUTES} minutes, "
    #         f"stay {settings.MEET_BOT_STAY_DURATION_SECONDS}s)"
    #     )

    # Debug: confirm Google OAuth is configured
    _gid = (settings.GOOGLE_CLIENT_ID or "").strip()
    _gsec = (settings.GOOGLE_CLIENT_SECRET or "").strip()
    _masked_id = (_gid[:8] + "..." + _gid[-8:]) if len(_gid) >= 16 else (_gid or "MISSING")
    _masked_sec = (_gsec[:4] + "****" + _gsec[-4:]) if len(_gsec) >= 8 else ("MISSING" if not _gsec else "****")
    logger.info(
        f"[Google OAuth] client_id={_masked_id} (len={len(_gid)}) | "
        f"client_secret={_masked_sec} (len={len(_gsec)}) | "
        f"redirect_uri={settings.GOOGLE_REDIRECT_URI}"
    )
    if not _gsec:
        logger.warning("[Google OAuth] GOOGLE_CLIENT_SECRET is not set — /auth/google/callback will fail")

    logger.info("Application started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    close_db()
    try:
        await close_redis()
    except Exception as e:
        logger.warning(f"Error closing Redis: {e}")

    if auto_sync_task:
        auto_sync_task.cancel()
        try:
            await auto_sync_task
        except asyncio.CancelledError:
            pass

    if retention_task:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass

    if zoom_auto_join_task:
        zoom_auto_join_task.cancel()
        try:
            await zoom_auto_join_task
        except asyncio.CancelledError:
            pass

    if auto_join_task:
        auto_join_task.cancel()
        try:
            await auto_join_task
        except asyncio.CancelledError:
            pass

    logger.info("Application shut down successfully")


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    description="AI-Powered Meeting Intelligence & Context Agent",
    version="1.0.0",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
    redoc_url=f"{settings.API_V1_PREFIX}/redoc",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    lifespan=lifespan,
)

# Add middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=settings.ALLOWED_METHODS,
    allow_headers=settings.ALLOWED_HEADERS,
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RateLimitMiddleware)

if not settings.DEBUG:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.TRUSTED_HOSTS
    )

# Include API routers
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint"""
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "status": "operational",
        "environment": settings.APP_ENV,
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    database_status = "connected"
    redis_status = "disconnected"
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:
        database_status = "error"
        logger.warning(f"Health database check failed: {exc}")

    try:
        if redis_client is not None:
            await redis_client.ping()
            redis_status = "connected"
    except Exception as exc:
        redis_status = "error"
        logger.warning(f"Health redis check failed: {exc}")

    return {
        "status": "healthy" if database_status == "connected" else "degraded",
        "database": database_status,
        "redis": redis_status,
        "environment": settings.APP_ENV,
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "request_id": request.state.request_id if hasattr(request.state, "request_id") else None,
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation failed",
            "errors": exc.errors(),
            "request_id": request.state.request_id if hasattr(request.state, "request_id") else None,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "request_id": request.state.request_id if hasattr(request.state, "request_id") else None,
        },
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
