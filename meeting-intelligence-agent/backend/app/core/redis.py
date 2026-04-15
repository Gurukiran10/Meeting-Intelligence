"""
Redis Connection and Caching
"""
from typing import Optional, Any
import json
import logging
from redis.asyncio import Redis, from_url
from app.core.config import settings

logger = logging.getLogger(__name__)
redis_client: Optional[Redis] = None


async def init_redis() -> None:
    """Initialize Redis connection"""
    global redis_client
    try:
        redis_client = await from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
        # Test connection
        await redis_client.ping()  # type: ignore
        logger.info("Redis connection established")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Continuing without Redis.")
        redis_client = None


async def close_redis() -> None:
    """Close Redis connection"""
    if redis_client:
        try:
            await redis_client.aclose()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.warning(f"Error closing Redis: {e}")



async def get_redis() -> Redis:
    """Get Redis client"""
    return redis_client  # type: ignore


async def cache_set(key: str, value: Any, expire: Optional[int] = None) -> None:
    """Set cache value"""
    if redis_client:
        await redis_client.set(
            key,
            json.dumps(value),
            ex=expire or settings.REDIS_CACHE_EXPIRY,
        )


async def cache_get(key: str) -> Optional[Any]:
    """Get cache value"""
    if redis_client:
        value = await redis_client.get(key)
        if value:
            return json.loads(value)
    return None


async def cache_delete(key: str) -> None:
    """Delete cache value"""
    if redis_client:
        await redis_client.delete(key)


async def cache_clear_pattern(pattern: str) -> None:
    """Clear cache by pattern"""
    if redis_client:
        keys = await redis_client.keys(pattern)
        if keys:
            await redis_client.delete(*keys)
