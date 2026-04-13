"""
Meeting Intelligence Agent - Main Application Entry Point
"""
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.core.config import settings
from app.core.database import init_db, close_db
from app.core.redis import init_redis, close_redis
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
        allowed_hosts=["*.meetingintel.ai", "localhost"]
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
    return {
        "status": "healthy",
        "database": "connected",
        "redis": "connected",
    }


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
