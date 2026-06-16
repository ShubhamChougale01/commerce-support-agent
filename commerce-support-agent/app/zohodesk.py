"""Async Zoho Desk API client.

Drop-in replacement for the old Freshdesk client: exposes the SAME five
function signatures and normalizes Zoho responses into the shapes downstream
code consumes:

    get_ticket()        -> { "id", "subject", "requester": {"email","name"},
                             "description_text" }
    get_ticket_thread() -> [ {"body_text","from_email","created_at","private"} ]

Auth: OAuth2. Access tokens expire after ~1 hour, so a small token manager
caches the current token and refreshes it via the stored refresh token
(Zoho "Self Client" flow). On a 401 the request is retried once with a
force-refreshed token.
"""

import asyncio
import html as _html
import re
import time

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import (
    ZOHO_CLIENT_ID,
    ZOHO_CLIENT_SECRET,
    ZOHO_DC,
    ZOHO_FROM_EMAIL,
    ZOHO_ORG_ID,
    ZOHO_REFRESH_TOKEN,
)

BASE_URL = f"https://desk.zoho.{ZOHO_DC}/api/v1"
ACCOUNTS_URL = f"https://accounts.zoho.{ZOHO_DC}/oauth/v2/token"

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)

# --------------------------------------------------------------------------- #
# OAuth token manager                                                          #
# --------------------------------------------------------------------------- #
_token: str | None = None
_token_expires_at: float = 0.0
_token_lock = asyncio.Lock()

# Refresh 2 minutes before the reported expiry to avoid edge-of-window 401s.
_EXPIRY_MARGIN_SECS = 120


async def _fetch_access_token() -> tuple[str, float]:
    """Exchange the refresh token for a fresh access token."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            ACCOUNTS_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": ZOHO_CLIENT_ID,
                "client_secret": ZOHO_CLIENT_SECRET,
                "refresh_token": ZOHO_REFRESH_TOKEN,
            },
        )
        resp.raise_for_status()
        body = resp.json()
    token = body["access_token"]
    expires_in = float(body.get("expires_in", 3600))
    return token, time.monotonic() + expires_in - _EXPIRY_MARGIN_SECS


async def _get_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing if expired or forced."""
    global _token, _token_expires_at
    async with _token_lock:
        if force_refresh or _token is None or time.monotonic() >= _token_expires_at:
            _token, _token_expires_at = await _fetch_access_token()
        return _token


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    """Make an authenticated request; on 401, refresh the token and retry once."""
    token = await _get_token()
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "orgId": ZOHO_ORG_ID,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=15.0) as client:
        resp = await client.request(method, path, headers=headers, **kwargs)
        if resp.status_code == 401:
            token = await _get_token(force_refresh=True)
            headers["Authorization"] = f"Zoho-oauthtoken {token}"
            resp = await client.request(method, path, headers=headers, **kwargs)
        return resp


# --------------------------------------------------------------------------- #
# Normalization helpers                                                        #
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _html.unescape(_TAG_RE.sub("", text or "")).strip()


def _normalize_ticket(raw: dict) -> dict:
    """Map a Zoho Desk ticket onto the shape downstream code expects."""
    contact = raw.get("contact") or {}
    first = contact.get("firstName") or ""
    last = contact.get("lastName") or ""
    name = f"{first} {last}".strip()
    normalized = dict(raw)  # keep all Zoho fields available
    normalized["requester"] = {
        "email": contact.get("email") or raw.get("email") or "",
        "name": name,
    }
    normalized["description_text"] = _strip_html(raw.get("description") or "")
    return normalized


def _normalize_conversation(entry: dict) -> dict:
    """Map a Zoho conversation entry (thread or comment) onto the
    {body_text, from_email, created_at, private} shape."""
    body = entry.get("content") or entry.get("summary") or entry.get("comment") or ""
    is_comment = entry.get("type") == "comment" or "isPublic" in entry
    private = (not entry.get("isPublic", True)) if is_comment else False
    commenter = entry.get("commenter") or {}
    from_email = (
        entry.get("fromEmailAddress")
        or commenter.get("email")
        or (entry.get("author") or {}).get("email")
        or ""
    )
    return {
        "body_text": _strip_html(body),
        "from_email": from_email,
        "created_at": entry.get("createdTime") or "",
        "private": private,
    }


# --------------------------------------------------------------------------- #
# Public API — same signatures as the old freshdesk client                     #
# --------------------------------------------------------------------------- #
@_retry
async def patch_ticket(ticket_id: int, payload: dict) -> dict:
    """Update a ticket (team, priority, status, ...). Zoho uses PATCH."""
    resp = await _request("PATCH", f"/tickets/{ticket_id}", json=payload)
    resp.raise_for_status()
    return resp.json()


@_retry
async def post_note(ticket_id: int, body_html: str, private: bool = True) -> dict:
    """Add a comment to a ticket. Private by default (internal-only)."""
    resp = await _request(
        "POST",
        f"/tickets/{ticket_id}/comments",
        json={
            "content": body_html,
            "contentType": "html",
            "isPublic": not private,
        },
    )
    resp.raise_for_status()
    return resp.json()


@_retry
async def post_reply(ticket_id: int, body_html: str) -> dict:
    """Send a public email reply to the ticket's requester.

    Zoho's sendReply needs explicit from/to addresses, so the requester email
    is resolved from the ticket first.
    """
    ticket = await get_ticket(ticket_id)
    to_email = ticket["requester"]["email"]
    resp = await _request(
        "POST",
        f"/tickets/{ticket_id}/sendReply",
        json={
            "channel": "EMAIL",
            "content": body_html,
            "contentType": "html",
            "fromEmailAddress": ZOHO_FROM_EMAIL,
            "to": to_email,
        },
    )
    resp.raise_for_status()
    return resp.json()


@_retry
async def get_ticket_thread(ticket_id: int) -> list[dict]:
    """Return the ticket's conversations, normalized, sorted oldest-first."""
    resp = await _request(
        "GET", f"/tickets/{ticket_id}/conversations", params={"limit": 50}
    )
    resp.raise_for_status()
    entries = resp.json().get("data", [])
    normalized = [_normalize_conversation(e) for e in entries]
    normalized.sort(key=lambda c: c["created_at"])
    return normalized


@_retry
async def get_ticket(ticket_id: int) -> dict:
    """Return the full ticket with the contact embedded and normalized."""
    resp = await _request(
        "GET", f"/tickets/{ticket_id}", params={"include": "contacts"}
    )
    resp.raise_for_status()
    return _normalize_ticket(resp.json())
