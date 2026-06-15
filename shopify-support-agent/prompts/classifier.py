"""Pass 1 — ticket classification.

Calls Claude with a strict JSON-only system prompt and returns the parsed
classification dict. On unrecoverable parse failure it degrades safely to an
escalation so a human always sees the ticket.
"""

import json

from app.config import CLASSIFIER_MODEL
from prompts._llm_client import get_client

# Anthropic API key if set, else the Claude Code subscription (Agent SDK).
_client = get_client()

CLASSIFIER_SYSTEM_PROMPT = """\
You are a customer support ticket classifier for an e-commerce (D2C) brand.

Your ONLY job is to return a single JSON object describing the ticket.
- NEVER write prose.
- NEVER address the customer.
- NEVER wrap the JSON in markdown fences or add commentary before/after it.
- Return ONLY the raw JSON object.

You receive a user message containing:
  TICKET         - the raw customer message
  CUSTOMER_EMAIL - the email the ticket was submitted from (may be empty)
  ORDER          - enriched order data as JSON (may be null)
  HISTORY        - the last 3 ticket summaries from this customer (may be an empty list)

Return EXACTLY this JSON schema (and nothing else):
{
  "intent": "order_status | refund_request | product_question | cancellation | exchange | complaint | other",
  "confidence": 0.0,
  "sub_intent": "optional string e.g. late_delivery or wrong_item",
  "sentiment": "positive | neutral | frustrated | angry",
  "urgency": "low | medium | high",
  "fraud_flags": {
    "triggered": false,
    "reasons": []
  },
  "refund_amount_inr": null,
  "requires_shopify_action": false,
  "shopify_action": "issue_refund | cancel_order | null",
  "escalate": false,
  "escalation_reason": null,
  "resolution_possible": true
}

FIELD GUIDANCE:
- confidence: your calibrated confidence (0.0-1.0) that the intent + plan are correct.
- refund_amount_inr: the rupee amount the customer is asking to be refunded, or null
  if no specific refund is requested. Infer from ORDER total when the customer asks
  for a full refund and an amount is available.
- requires_shopify_action / shopify_action: set when the resolution needs an
  order-system mutation. Use "issue_refund" for refunds and "cancel_order" for
  cancellations. Otherwise shopify_action must be null.

FRAUD FLAGS - set fraud_flags.triggered = true and add a short string to
fraud_flags.reasons for ANY of these:
- CUSTOMER_EMAIL is non-empty and does NOT match the email on the ORDER
  (compare case-insensitively; ignore this rule when either value is missing).
- More than 3 support tickets on different orders in the last 30 days (from HISTORY).
- A refund is requested within 2 hours of delivery completion.
- Chargeback history is present in the ORDER data.

ESCALATION - set escalate = true and put the matching reason in escalation_reason
(one of: low_confidence, fraud_flag, refund_above_threshold, legal_threat,
human_requested, abusive_language) for ANY of these:
- confidence is below 0.75  -> "low_confidence"
- fraud_flags.triggered is true  -> "fraud_flag"
- sentiment is "angry" AND refund_amount_inr is above 2000  -> "refund_above_threshold"
- legal threat language is present (sue, lawyer, consumer court, police,
  legal action)  -> "legal_threat"
- the customer asks to speak to a human / real person  -> "human_requested"
- this is the 3rd or more ticket on the SAME order (from HISTORY)  -> "human_requested"

If multiple escalation conditions apply, prefer the most severe in this order:
fraud_flag > legal_threat > refund_above_threshold > human_requested >
abusive_language > low_confidence.

Return only the JSON object.
"""


def _default_escalation(reason: str = "classifier_error") -> dict:
    """A safe classification that forces a human handoff."""
    return {
        "intent": "other",
        "confidence": 0.0,
        "sub_intent": None,
        "sentiment": "neutral",
        "urgency": "medium",
        "fraud_flags": {"triggered": False, "reasons": []},
        "refund_amount_inr": None,
        "requires_shopify_action": False,
        "shopify_action": None,
        "escalate": True,
        "escalation_reason": reason,
        "resolution_possible": False,
    }


def _build_user_message(
    ticket_body: str, order_data: dict | None, history: list, customer_email: str = ""
) -> str:
    return (
        f"TICKET:\n{ticket_body}\n\n"
        f"CUSTOMER_EMAIL:\n{customer_email or '(unknown)'}\n\n"
        f"ORDER:\n{json.dumps(order_data, ensure_ascii=False) if order_data else 'null'}\n\n"
        f"HISTORY:\n{json.dumps(history, ensure_ascii=False)}"
    )


def _extract_json(text: str) -> dict:
    """Parse a JSON object from the model output, tolerating stray text or
    markdown fences around it. Raises ValueError if no object can be parsed."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: grab the outermost {...} span.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found in model output")


async def classify_ticket(
    ticket_body: str,
    order_data: dict | None,
    history: list,
    customer_email: str = "",
) -> dict:
    """Classify a ticket. Returns the parsed classification dict, or a default
    escalation dict if the model cannot produce valid JSON after one retry.

    customer_email is the address the ticket was submitted from; passing it lets
    the model compare against the ORDER email for the fraud mismatch rule rather
    than guessing. Defaults to "" so existing callers/tests stay valid."""
    user_message = _build_user_message(
        ticket_body, order_data, history, customer_email
    )
    messages = [{"role": "user", "content": user_message}]

    # First attempt.
    try:
        resp = await _client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=512,
            system=CLASSIFIER_SYSTEM_PROMPT,
            messages=messages,
        )
        return _extract_json(resp.content[0].text)
    except (ValueError, json.JSONDecodeError):
        pass
    except Exception:
        # Network/API failure -> escalate rather than crash the pipeline.
        return _default_escalation("classifier_error")

    # Retry once, explicitly demanding valid JSON.
    messages.append(
        {
            "role": "user",
            "content": "Your previous response was not valid JSON. "
            "Return ONLY the raw JSON object, with no other text.",
        }
    )
    try:
        resp = await _client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=512,
            system=CLASSIFIER_SYSTEM_PROMPT,
            messages=messages,
        )
        return _extract_json(resp.content[0].text)
    except Exception:
        return _default_escalation("classifier_error")
