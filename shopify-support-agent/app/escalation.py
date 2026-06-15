"""Escalation handler — hand a ticket off to a human team.

Composes: an AI handoff brief (Haiku), a customer acknowledgement, a private
HTML note, Zoho Desk reassignment, a Supabase audit row, and (for P1) a Slack
alert. The Supabase and Anthropic clients are created lazily so this module
imports cleanly even when those services aren't configured.
"""

import asyncio
import html
import sys

import httpx

from app.brand_config import BrandConfig
from app.config import (
    HANDOFF_MODEL,
    PRIORITY_MAP,
    SLACK_WEBHOOK_URL,
    SUPABASE_KEY,
    SUPABASE_URL,
    get_route,
)
from app.zohodesk import patch_ticket, post_note, post_reply
from prompts._llm_client import get_client

# Anthropic API key if set, else the Claude Code subscription (Agent SDK).
_anthropic = get_client()

# Lazily-created Supabase client (import is optional at module load time).
_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client

        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


_HANDOFF_SYSTEM_PROMPT = """\
You are writing a 3-sentence internal note for a human support agent who is
taking over this ticket. Write exactly three sentences:
Sentence 1: the customer's exact problem and their order situation.
Sentence 2: what the AI agent already confirmed or attempted.
Sentence 3: exactly what the human agent needs to do next.
Use direct language. No filler. No empathy language. This is for a colleague,
not the customer. Do not address the customer. Output only the three sentences.
"""


async def generate_handoff_brief(
    ticket_body: str, classification: dict, order_data: dict | None
) -> str:
    """Generate a terse 3-sentence brief for the human taking over."""
    import json

    user_message = (
        f"CUSTOMER_MESSAGE:\n{ticket_body}\n\n"
        f"CLASSIFICATION:\n{json.dumps(classification, ensure_ascii=False)}\n\n"
        f"ORDER:\n{json.dumps(order_data, ensure_ascii=False) if order_data else 'null'}"
    )
    try:
        resp = await _anthropic.messages.create(
            model=HANDOFF_MODEL,
            max_tokens=150,
            system=_HANDOFF_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:  # never block an escalation on brief generation
        print(f"[escalation] handoff brief failed: {exc}", file=sys.stderr)
        return "Brief unavailable (generation failed). Review the ticket and order details below."


def build_customer_ack(
    first_name: str, order_id: str, sla_hrs: int, sign_off: str
) -> str:
    """Plain-text acknowledgement sent to the customer. Never mentions AI/bot."""
    name = first_name or "there"
    order_ref = f" regarding your order {order_id}" if order_id else ""
    return (
        f"Hi {name},\n\n"
        f"Thanks for reaching out{order_ref}. I've passed this to a specialist "
        f"on our team who will personally follow up within {sla_hrs} hours. "
        f"There's nothing you need to do in the meantime — we'll take it from here.\n\n"
        f"{sign_off}"
    )


def build_note_html(
    brief: str,
    classification: dict,
    order_data: dict | None,
    reason: str,
    draft: str | None,
) -> str:
    """Build the private HTML note attached to the ticket for the human agent."""
    confidence = classification.get("confidence")
    fraud_flags = classification.get("fraud_flags", {})

    order_number = total_price = fulfillment_status = "—"
    if order_data:
        order_number = order_data.get("order_number") or order_data.get("name") or "—"
        total_price = order_data.get("total_price") or "—"
        fulfillment_status = order_data.get("fulfillment_status") or "unfulfilled"

    parts = [
        f"<h3>AI Agent Handoff &mdash; <code>{html.escape(str(reason))}</code></h3>",
        f"<p><strong>Confidence:</strong> {html.escape(str(confidence))}</p>",
        f"<p><strong>Fraud flags:</strong> {html.escape(str(fraud_flags))}</p>",
        f"<p><strong>Brief:</strong> {html.escape(brief)}</p>",
        "<h4>Order snapshot</h4>",
        (
            f"<p>Order: {html.escape(str(order_number))} | "
            f"Total: &#8377;{html.escape(str(total_price))} | "
            f"Status: {html.escape(str(fulfillment_status))}</p>"
        ),
    ]
    if draft:
        parts.append(
            f"<blockquote><strong>Rejected draft:</strong><br>"
            f"{html.escape(draft)}</blockquote>"
        )
    return "\n".join(parts)


async def log_escalation(data: dict) -> None:
    """Insert an audit row into the Supabase `escalations` table."""
    try:
        _get_supabase().table("escalations").insert(data).execute()
    except Exception as exc:
        print(f"[escalation] supabase log failed: {exc}", file=sys.stderr)


async def send_slack_alert(
    channel: str, ticket_id: int, reason: str, sla_hrs: int, customer_name: str
) -> None:
    """Post an alert to the configured Slack webhook."""
    if not SLACK_WEBHOOK_URL:
        return
    route = get_route(reason)
    is_p1 = route["priority"] == 1
    header = ":rotating_light: P1 Escalation" if is_p1 else ":warning: SLA warning"
    text = (
        f"{header}\n"
        f"*Ticket:* #{ticket_id}\n"
        f"*Customer:* {customer_name}\n"
        f"*Reason:* `{reason}`\n"
        f"*Window:* {sla_hrs} hr SLA"
    )
    payload = {"channel": channel, "text": text}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(SLACK_WEBHOOK_URL, json=payload)
    except Exception as exc:
        print(f"[escalation] slack alert failed: {exc}", file=sys.stderr)


def _utcnow_iso() -> str:
    # Imported here so the unavailable-in-some-sandboxes datetime call is local.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def escalate_to_human(
    ticket: dict,
    classification: dict,
    order_data: dict | None,
    brand_config: BrandConfig,
    reason: str | None = None,
    draft: str | None = None,
) -> None:
    """Route a ticket to the correct human team with full context."""
    route = get_route(reason)
    team_id = route["team_id"]
    priority = route["priority"]
    sla_hrs = route["sla_hrs"]
    slack_channel = route["slack_channel"]

    ticket_id = ticket.get("id")
    requester = ticket.get("requester") or {}
    customer_name = requester.get("name") or ticket.get("name") or ""
    customer_email = requester.get("email") or ticket.get("email") or ""
    first_name = customer_name.split(" ")[0] if customer_name else ""
    order_id = ""
    if order_data:
        order_id = str(
            order_data.get("order_number") or order_data.get("name") or ""
        )

    ticket_body = ticket.get("description_text") or ticket.get("description") or ""

    # Independent generation work runs concurrently.
    brief, customer_ack = await asyncio.gather(
        generate_handoff_brief(ticket_body, classification, order_data),
        # build_customer_ack is sync but cheap; wrap to keep the gather symmetric.
        asyncio.to_thread(
            build_customer_ack, first_name, order_id, sla_hrs, brand_config.sign_off
        ),
    )

    note_html = build_note_html(brief, classification, order_data, reason or "unknown", draft)

    # Sequential Zoho Desk mutations (order matters for the agent UX).
    # Tags are dropped (D2): Zoho's PATCH has no simple tags field; the reason
    # is already prominent in the private note and the Supabase row.
    await patch_ticket(
        ticket_id,
        {
            "teamId": team_id,
            "priority": PRIORITY_MAP[priority],
        },
    )
    await post_note(ticket_id, note_html, private=True)
    await post_reply(ticket_id, f"<p>{html.escape(customer_ack)}</p>")

    ack_sent_at = _utcnow_iso()
    fraud_flags = classification.get("fraud_flags", {})
    await log_escalation(
        {
            "ticket_id": str(ticket_id),
            "client_id": brand_config.client_id,
            "customer_email": customer_email,
            "escalation_reason": reason or "unknown",
            "confidence": classification.get("confidence"),
            "fraud_flags": fraud_flags,
            "refund_amount": classification.get("refund_amount_inr"),
            "agent_group": str(team_id),
            "priority": priority,
            "sla_hrs": sla_hrs,
            "handoff_brief": brief,
            "customer_ack_sent_at": ack_sent_at,
            "slack_warned": False,
            "resolved_by": None,
            "resolved_at": None,
            "human_edited_draft": False,
        }
    )

    if priority == 1:
        await send_slack_alert(slack_channel, ticket_id, reason or "unknown", sla_hrs, customer_name)
