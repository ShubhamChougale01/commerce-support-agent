"""Main orchestrator — run_agent().

Drives a single ticket through the full pipeline: fetch -> enrich -> classify
-> (escalate?) -> act -> reply -> quality gate -> send/log. Any failure along
the way is caught and routed to a human as a `system_error` escalation so a
ticket is never silently dropped.
"""

import re
import sys

from app.brand_config import get_brand_config
from app.escalation import escalate_to_human
from app.medusa import get_order_by_email, get_order_by_id, issue_refund
from app.quality_gate import check_reply
from app.zohodesk import get_ticket, get_ticket_thread, post_reply
from prompts.classifier import classify_ticket
from prompts.reply_generator import generate_reply

_ORDER_ID_RE = re.compile(r"#(\d+)")

# Lazily-created Supabase client (shared pattern with escalation.py).
_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client

        from app.config import SUPABASE_KEY, SUPABASE_URL

        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def _extract_order_id(subject: str, body: str) -> str | None:
    """Pull the first #<digits> token from the subject, then the body."""
    for source in (subject or "", body or ""):
        match = _ORDER_ID_RE.search(source)
        if match:
            return match.group(1)
    return None


async def run_agent(ticket_id: int, client_id: str = "default") -> None:
    brand_config = get_brand_config(client_id)

    # Held for the except-block so we can still escalate with whatever context
    # we managed to gather before failing.
    ticket: dict = {"id": ticket_id}
    classification: dict = {}
    order_data: dict | None = None

    try:
        # --- Step 1: fetch the ticket -------------------------------------- #
        ticket = await get_ticket(ticket_id)
        requester = ticket.get("requester") or {}
        customer_email = requester.get("email") or ticket.get("email") or ""
        customer_name = requester.get("name") or ticket.get("name") or ""
        ticket_body = (
            ticket.get("description_text") or ticket.get("description") or ""
        )
        subject = ticket.get("subject") or ""
        order_id = _extract_order_id(subject, ticket_body)

        # --- Step 2: enrich with Medusa order data -------------------------- #
        if order_id:
            order_data = await get_order_by_id(order_id)
        if order_data is None and customer_email:
            order_data = await get_order_by_email(customer_email)

        # --- Step 3: ticket thread (multi-turn context) -------------------- #
        ticket_thread = await get_ticket_thread(ticket_id)

        # --- Step 4: classify ---------------------------------------------- #
        classification = await classify_ticket(
            ticket_body, order_data, history=[], customer_email=customer_email
        )

        # --- Step 5: hard escalation gate ---------------------------------- #
        fraud_triggered = classification.get("fraud_flags", {}).get("triggered", False)
        if classification.get("escalate") or fraud_triggered:
            reason = classification.get("escalation_reason") or (
                "fraud_flag" if fraud_triggered else "low_confidence"
            )
            await escalate_to_human(
                ticket, classification, order_data, brand_config, reason=reason
            )
            return

        # --- Step 6: commerce action (key names kept per D3) ---------------- #
        action_taken: str | None = None
        commerce_action = classification.get("commerce_action")

        if commerce_action == "issue_refund":
            amount = classification.get("refund_amount_inr") or 0
            if amount <= brand_config.auto_refund_limit:
                await issue_refund(
                    order_id or "", float(amount), reason="Customer support auto-refund"
                )
                action_taken = f"refund of ₹{amount} issued"
            else:
                await escalate_to_human(
                    ticket,
                    classification,
                    order_data,
                    brand_config,
                    reason="refund_above_threshold",
                )
                return
        elif commerce_action == "cancel_order":
            # Never auto-cancel.
            await escalate_to_human(
                ticket,
                classification,
                order_data,
                brand_config,
                reason="human_requested",
            )
            return

        # --- Step 7: generate reply ---------------------------------------- #
        reply_draft = await generate_reply(
            classification,
            order_data,
            action_taken,
            ticket_thread,
            ticket_body,
            brand_config,
        )

        # --- Step 8: quality gate ------------------------------------------ #
        gate = check_reply(reply_draft, brand_config)
        if not gate.passed:
            await escalate_to_human(
                ticket,
                classification,
                order_data,
                brand_config,
                reason="quality_gate_fail",
                draft=reply_draft,
            )
            return

        # --- Step 9: send + log -------------------------------------------- #
        await post_reply(ticket_id, f"<p>{reply_draft}</p>")
        _log_processed(
            ticket_id=ticket_id,
            client_id=client_id,
            classification=classification,
            action_taken=action_taken,
        )

    except Exception as exc:
        print(f"[agent] run_agent failed for ticket {ticket_id}: {exc!r}", file=sys.stderr)
        try:
            await escalate_to_human(
                ticket, classification, order_data, brand_config, reason="system_error"
            )
        except Exception as esc_exc:
            print(
                f"[agent] escalation ALSO failed for ticket {ticket_id}: {esc_exc!r}",
                file=sys.stderr,
            )


def _log_processed(
    ticket_id: int, client_id: str, classification: dict, action_taken: str | None
) -> None:
    """Insert a row into the `tickets_processed` analytics table. Best-effort;
    a logging failure must not turn a sent reply into a system_error."""
    try:
        _get_supabase().table("tickets_processed").insert(
            {
                "ticket_id": str(ticket_id),
                "client_id": client_id,
                "intent": classification.get("intent"),
                "confidence": classification.get("confidence"),
                "action_taken": action_taken,
                # TODO: surface real token usage from the prompt calls.
                "tokens_used": None,
            }
        ).execute()
    except Exception as exc:
        print(f"[agent] tickets_processed log failed: {exc}", file=sys.stderr)
