"""SLA watchdog.

Periodically scans unresolved escalations and fires a Slack warning once a
ticket crosses 75% of its SLA window, so a human can act before breach.
"""

import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_route
from app.escalation import send_slack_alert

# Lazily-created Supabase client (shared pattern with the rest of the app).
_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client

        from app.config import SUPABASE_KEY, SUPABASE_URL

        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def _parse_ts(value: str):
    """Parse an ISO timestamp (tolerating a trailing Z) into an aware datetime."""
    from datetime import datetime

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


async def sla_watchdog() -> None:
    """Warn on escalations that have crossed 75% of their SLA window."""
    from datetime import datetime, timezone

    try:
        rows = (
            _get_supabase()
            .table("escalations")
            .select("*")
            .is_("resolved_at", "null")
            .eq("slack_warned", False)
            .execute()
            .data
        )
    except Exception as exc:
        print(f"[watchdog] query failed: {exc}", file=sys.stderr)
        return

    now = datetime.now(timezone.utc)
    for row in rows or []:
        escalated_at = row.get("escalated_at")
        sla_hrs = row.get("sla_hrs")
        if not escalated_at or not sla_hrs:
            continue
        try:
            started = _parse_ts(escalated_at)
        except (ValueError, TypeError):
            continue

        warn_after_secs = sla_hrs * 0.75 * 3600
        elapsed_secs = (now - started).total_seconds()
        if elapsed_secs < warn_after_secs:
            continue

        breach_secs = sla_hrs * 3600
        minutes_remaining = max(0, int((breach_secs - elapsed_secs) // 60))

        reason = row.get("escalation_reason") or "unknown"
        channel = get_route(reason)["slack_channel"]
        await send_slack_alert(
            channel,
            row.get("ticket_id"),
            reason,
            minutes_remaining,
            row.get("customer_email") or "customer",
        )

        try:
            _get_supabase().table("escalations").update(
                {"slack_warned": True}
            ).eq("id", row.get("id")).execute()
        except Exception as exc:
            print(f"[watchdog] mark-warned failed for {row.get('id')}: {exc}", file=sys.stderr)


def start_watchdog(app) -> None:
    """Create and start the interval scheduler. Stashes it on app.state so the
    lifespan handler can shut it down cleanly."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(sla_watchdog, "interval", minutes=15, id="sla_watchdog")
    scheduler.start()
    app.state.scheduler = scheduler
