"""FastAPI entrypoint.

Exposes the Zoho Desk webhook (shared-secret verified + idempotent) and a
health check. Starts the SLA watchdog in the lifespan context and shuts it
down on exit.

Webhook contract (configured in Zoho Desk -> Setup -> Automation -> Workflows
-> Webhooks): on Ticket Create, POST to /webhook/desk with
  - header  X-Webhook-Secret: <DESK_WEBHOOK_SECRET>
  - body    {"ticket_id": "${ticketId}"}
Zoho workflow webhooks cannot HMAC-sign the body (D5), so auth is a
constant-time comparison of the shared-secret header over HTTPS.
"""

import hmac
import sys
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response

from app.agent import run_agent
from app.config import DESK_WEBHOOK_SECRET
from app.watchdog import start_watchdog

# Lazily-created Supabase client (shared pattern across the app).
_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client

        from app.config import SUPABASE_KEY, SUPABASE_URL

        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_watchdog(app)
    try:
        yield
    finally:
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="Commerce Support Agent", lifespan=lifespan)


def _verify_secret(provided: str | None) -> bool:
    """Constant-time comparison of the shared-secret header."""
    if not provided or not DESK_WEBHOOK_SECRET:
        return False
    return hmac.compare_digest(DESK_WEBHOOK_SECRET, provided)


def _already_processed(ticket_id) -> bool:
    rows = (
        _get_supabase()
        .table("processed_tickets")
        .select("ticket_id")
        .eq("ticket_id", str(ticket_id))
        .execute()
        .data
    )
    return bool(rows)


def _mark_processed(ticket_id, client_id: str = "default") -> None:
    _get_supabase().table("processed_tickets").insert(
        {"ticket_id": str(ticket_id), "client_id": client_id}
    ).execute()


@app.post("/webhook/desk")
async def desk_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> Response:
    if not _verify_secret(request.headers.get("X-Webhook-Secret")):
        return Response(status_code=401, content="invalid secret")

    payload = await request.json()
    # Our Zoho workflow sends a flat body: {"ticket_id": "..."}.
    ticket_id = payload.get("ticket_id")
    if ticket_id is None:
        return Response(status_code=400, content="missing ticket_id")

    # Idempotency: the workflow may fire more than once for a ticket.
    try:
        if _already_processed(ticket_id):
            return Response(status_code=200, content="already processed")
        _mark_processed(ticket_id)
    except Exception as exc:
        # If the idempotency store is unreachable, fail closed (don't risk a
        # duplicate auto-refund) and let the desk retry.
        print(f"[webhook] idempotency check failed: {exc}", file=sys.stderr)
        return Response(status_code=503, content="idempotency store unavailable")

    background_tasks.add_task(run_agent, ticket_id)
    return Response(status_code=200, content="accepted")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8001)), reload=True)
