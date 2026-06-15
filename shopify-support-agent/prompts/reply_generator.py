"""Pass 2 — customer reply generation.

Builds a brand-aware system prompt and generates the email reply body Claude
will send to the customer. The reply is plain text (no subject, no JSON); the
agent wraps it in <p> tags before handing it to Zoho Desk.
"""

import json

from app.brand_config import BrandConfig
from app.config import REPLY_MODEL
from prompts._llm_client import get_client

# Anthropic API key if set, else the Claude Code subscription (Agent SDK).
_client = get_client()


def build_reply_prompt(brand_config: BrandConfig) -> str:
    """Return the system prompt for reply generation, with brand voice,
    policy, and output rules injected."""
    non_refundable = ", ".join(brand_config.non_refundable) or "none"
    return f"""\
You are a customer support agent for {brand_config.brand_name}. You write the
email reply that goes directly to the customer.

BRAND VOICE:
{brand_config.voice}

TONE EXAMPLE (match this feel, do not copy it verbatim):
{brand_config.tone_example}

POLICY (use these facts; never invent others):
- Refunds are accepted within {brand_config.refund_days} days of delivery.
- Exchanges are accepted within {brand_config.exchange_days} days of delivery.
- Non-refundable items: {non_refundable}.
- Refunds up to INR {brand_config.auto_refund_limit} can be processed immediately.

OUTPUT RULES:
- Write ONLY the email reply body. No subject line, no JSON, no commentary,
  no headers, no labels.
- Start with a greeting and end with this exact sign-off:
{brand_config.sign_off}
- Keep it to 3-5 sentences for simple resolutions; up to 8 for complex ones.

TONE RULES:
- Match the customer's language. If they write in Hindi, reply in Hindi.
- If the customer's sentiment is frustrated or angry, lead with genuine empathy
  before any facts or logistics.
- Never say "as per our policy" or similar bureaucratic phrasing.
- Never be defensive. Own the situation and focus on the fix.
- Only state actions as done if ACTION_TAKEN confirms they were done.
"""


def _format_thread(ticket_thread: list[dict]) -> str:
    """Render the last 3 messages of the thread as a readable transcript so
    Claude has multi-turn context."""
    if not ticket_thread:
        return "(no prior messages)"
    recent = ticket_thread[-3:]
    lines = []
    for msg in recent:
        sender = msg.get("from_email") or "unknown"
        # Private notes are internal; mark them so the model doesn't quote them.
        role = "INTERNAL NOTE" if msg.get("private") else sender
        body = (msg.get("body_text") or "").strip()
        lines.append(f"[{role}]: {body}")
    return "\n".join(lines)


def _tracking_info(order_data: dict | None) -> str:
    """Pull a compact tracking summary out of order data, if present."""
    if not order_data:
        return "none"
    fulfillments = order_data.get("fulfillments") or []
    if not fulfillments:
        return "none"
    latest = fulfillments[-1]
    numbers = latest.get("tracking_numbers") or []
    urls = latest.get("tracking_urls") or []
    return json.dumps(
        {
            "status": latest.get("shipment_status") or latest.get("status"),
            "tracking_number": numbers[0] if numbers else None,
            "tracking_url": urls[0] if urls else None,
            "estimated_delivery": latest.get("estimated_delivery_at"),
        },
        ensure_ascii=False,
    )


async def generate_reply(
    classification: dict,
    order_data: dict | None,
    action_taken: str | None,
    ticket_thread: list[dict],
    ticket_body: str,
    brand_config: BrandConfig,
) -> str:
    """Generate the customer-facing reply body (plain text)."""
    user_message = (
        f"INTENT: {classification.get('intent')}\n"
        f"SENTIMENT: {classification.get('sentiment')}\n"
        f"ORDER_DATA: {json.dumps(order_data, ensure_ascii=False) if order_data else 'none'}\n"
        f"ACTION_TAKEN: {action_taken or 'none'}\n"
        f"TRACKING_INFO: {_tracking_info(order_data)}\n"
        f"CUSTOMER_MESSAGE:\n{ticket_body}\n\n"
        f"CONVERSATION_HISTORY:\n{_format_thread(ticket_thread)}"
    )

    resp = await _client.messages.create(
        model=REPLY_MODEL,
        max_tokens=800,
        system=build_reply_prompt(brand_config),
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text.strip()
