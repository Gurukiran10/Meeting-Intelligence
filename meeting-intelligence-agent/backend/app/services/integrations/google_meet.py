"""
Google Meet Integration Service

Uses the Google Meet REST API (v2) and Google Calendar API to:
- List Meet spaces (conference rooms / meeting codes)
- Fetch recordings and transcripts for a Meet space
- Sync upcoming calendar events that have a Meet link
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MEET_API_BASE = "https://meet.googleapis.com/v2"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleMeetService:
    """Thin async wrapper around the Google Meet REST API."""

    # ── Meet Spaces ──────────────────────────────────────────────────────────

    async def list_spaces(self, access_token: str) -> List[Dict[str, Any]]:
        """
        List all Meet spaces the authenticated user has access to.
        Returns a list of space resource dicts.
        """
        spaces: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        async with httpx.AsyncClient() as client:
            while True:
                params: Dict[str, Any] = {"pageSize": 100}
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(
                    f"{MEET_API_BASE}/spaces",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )

                if resp.status_code == 403:
                    logger.warning(
                        "Google Meet API returned 403 – the OAuth token may be missing "
                        "the 'meetings.space.readonly' scope. Re-connect Google to grant it."
                    )
                    break

                if resp.status_code != 200:
                    logger.error("Failed to list Meet spaces: %s", resp.text)
                    break

                data = resp.json()
                spaces.extend(data.get("spaces") or [])
                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return spaces

    async def get_space(self, access_token: str, space_name: str) -> Optional[Dict[str, Any]]:
        """
        Get a single Meet space by resource name (e.g. 'spaces/abc-defg-hij').
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{MEET_API_BASE}/{space_name}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            logger.error("Failed to get Meet space %s: %s", space_name, resp.text)
            return None
        return resp.json()

    # ── Recordings ───────────────────────────────────────────────────────────

    async def list_recordings(
        self, access_token: str, space_name: str
    ) -> List[Dict[str, Any]]:
        """
        List recordings for a Meet space.
        space_name: resource name like 'spaces/abc-defg-hij'
        """
        recordings: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        async with httpx.AsyncClient() as client:
            while True:
                params: Dict[str, Any] = {"pageSize": 50}
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(
                    f"{MEET_API_BASE}/{space_name}/recordings",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )

                if resp.status_code != 200:
                    logger.error(
                        "Failed to list recordings for %s: %s", space_name, resp.text
                    )
                    break

                data = resp.json()
                recordings.extend(data.get("recordings") or [])
                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return recordings

    # ── Transcripts ──────────────────────────────────────────────────────────

    async def list_transcripts(
        self, access_token: str, space_name: str
    ) -> List[Dict[str, Any]]:
        """
        List transcripts for a Meet space.
        """
        transcripts: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        async with httpx.AsyncClient() as client:
            while True:
                params: Dict[str, Any] = {"pageSize": 50}
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(
                    f"{MEET_API_BASE}/{space_name}/transcripts",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )

                if resp.status_code != 200:
                    logger.error(
                        "Failed to list transcripts for %s: %s", space_name, resp.text
                    )
                    break

                data = resp.json()
                transcripts.extend(data.get("transcripts") or [])
                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return transcripts

    async def list_transcript_entries(
        self, access_token: str, transcript_name: str
    ) -> List[Dict[str, Any]]:
        """
        List individual transcript entries (utterances) for a transcript resource.
        transcript_name: e.g. 'spaces/abc/transcripts/xyz'
        """
        entries: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        async with httpx.AsyncClient() as client:
            while True:
                params: Dict[str, Any] = {"pageSize": 100}
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(
                    f"{MEET_API_BASE}/{transcript_name}/entries",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )

                if resp.status_code != 200:
                    logger.error(
                        "Failed to list transcript entries for %s: %s",
                        transcript_name,
                        resp.text,
                    )
                    break

                data = resp.json()
                entries.extend(data.get("transcriptEntries") or [])
                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return entries

    # ── Calendar helpers ─────────────────────────────────────────────────────

    async def list_upcoming_meet_events(
        self,
        access_token: str,
        calendar_id: str = "primary",
        days_ahead: int = 30,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch upcoming calendar events that contain a Google Meet link.
        """
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days_ahead)

        params: Dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max_results,
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "timeMax": time_max.isoformat().replace("+00:00", "Z"),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )

        if resp.status_code != 200:
            logger.error("Failed to fetch calendar events: %s", resp.text)
            return []

        events = resp.json().get("items", [])

        # Filter to only events with a Google Meet link
        meet_events = []
        for event in events:
            hangout_link = event.get("hangoutLink")
            conference_data = event.get("conferenceData") or {}
            entry_points = conference_data.get("entryPoints") or []
            meet_entry = next(
                (
                    ep
                    for ep in entry_points
                    if ep.get("entryPointType") == "video"
                    and "meet.google.com" in str(ep.get("uri") or "")
                ),
                None,
            )
            if hangout_link or meet_entry:
                event["_meet_url"] = hangout_link or (meet_entry or {}).get("uri")
                meet_events.append(event)

        return meet_events

    async def list_calendars(self, access_token: str) -> List[Dict[str, Any]]:
        """List all calendars accessible to the user."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CALENDAR_API_BASE}/users/me/calendarList",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"maxResults": 100},
            )
        if resp.status_code != 200:
            logger.error("Failed to list calendars: %s", resp.text)
            return []
        return resp.json().get("items", [])


# Global singleton
google_meet_service = GoogleMeetService()
