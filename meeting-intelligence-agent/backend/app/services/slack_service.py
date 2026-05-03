"""
Simple Slack Notification Service
"""
import os
import logging
from slack_sdk import WebClient

logger = logging.getLogger(__name__)

_GLOBAL_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")


def _client(token: str | None = None) -> WebClient:
    t = token or _GLOBAL_TOKEN
    return WebClient(token=t) if t else None


def send_slack_dm(slack_user_id: str, message: str, bot_token: str | None = None):
    """
    Send a direct message to a Slack user.
    Uses bot_token if provided, falls back to SLACK_BOT_TOKEN env var.
    Silent on failure — never crashes the caller.
    """
    if not slack_user_id:
        return
    c = _client(bot_token)
    if not c:
        logger.warning("send_slack_dm: no Slack token available — skipping DM to %s", slack_user_id)
        return
    try:
        c.chat_postMessage(channel=slack_user_id, text=message)
        logger.info("send_slack_dm: DM sent to %s", slack_user_id)
    except Exception as exc:
        logger.warning("send_slack_dm: failed for %s: %s", slack_user_id, exc)
