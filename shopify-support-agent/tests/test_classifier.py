"""Classifier tests.

These call the live Claude API (classify_ticket), so they require a real
ANTHROPIC_API_KEY and are marked `integration` — skip in CI with
`-m "not integration"`.

Each fixture asserts the *intent* and *escalate* outcome. Confidence-style
assertions are kept loose (the model is non-deterministic); we assert the
decision, not exact scores.
"""

import pytest

from prompts.classifier import classify_ticket

pytestmark = pytest.mark.integration


# Minimal normalized-order helpers for fixtures (same shape medusa.py emits).
def _order(email="customer@example.com", total="500.00", **extra):
    base = {
        "id": 111,
        "order_number": 1234,
        "name": "#1234",
        "email": email,
        "total_price": total,
        "fulfillment_status": "fulfilled",
    }
    base.update(extra)
    return base


# (id, body, order_data, expected_intent, expected_escalate)
FIXTURES = [
    (
        "order_status",
        "Hi, where is my order #1234? It's been a week.",
        _order(),
        "order_status",
        False,
    ),
    (
        "refund_under_2k",
        "I'd like a refund for order #1234, the size didn't fit. It was ₹500.",
        _order(total="500.00"),
        "refund_request",
        False,
    ),
    (
        "refund_over_2k",
        "This is unacceptable, I demand a full refund of ₹5000 for order #1234 right now!",
        _order(total="5000.00"),
        "refund_request",
        True,
    ),
    (
        "legal_threat",
        "If you don't refund me I will take you to consumer court and get a lawyer.",
        _order(),
        "complaint",
        True,
    ),
    (
        "fraud_email_mismatch",
        "Refund my order #1234 to a different account please.",
        _order(email="someone.else@otherdomain.com"),
        "refund_request",
        True,
    ),
    (
        "product_question",
        "Does the cotton kurta come in navy blue, and is it pre-shrunk?",
        None,
        "product_question",
        False,
    ),
    (
        "ambiguous_low_confidence",
        "asdf?? thing broke. idk. help???",
        None,
        "other",
        True,
    ),
    (
        "hindi_order_status",
        "mera order kahan hai bhai? #1234",
        _order(),
        "order_status",
        False,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id,body,order_data,expected_intent,expected_escalate",
    FIXTURES,
    ids=[f[0] for f in FIXTURES],
)
async def test_classify(case_id, body, order_data, expected_intent, expected_escalate):
    # The fraud fixture exercises the email-mismatch rule, which now requires a
    # CUSTOMER_EMAIL that differs from the order's email (the model no longer
    # guesses when it's absent). Other cases need no requester email.
    customer_email = (
        "real.customer@example.com" if case_id == "fraud_email_mismatch" else ""
    )
    result = await classify_ticket(
        body, order_data, history=[], customer_email=customer_email
    )

    # Structural guarantees regardless of case.
    assert isinstance(result, dict)
    assert "intent" in result and "escalate" in result
    assert "fraud_flags" in result and "triggered" in result["fraud_flags"]

    assert result["intent"] == expected_intent, (
        f"[{case_id}] intent {result['intent']!r} != {expected_intent!r}"
    )

    # The fraud case is satisfied by either the fraud flag or an escalation.
    if case_id == "fraud_email_mismatch":
        assert result["fraud_flags"]["triggered"] or result["escalate"], (
            f"[{case_id}] expected fraud flag or escalation"
        )
        return

    assert bool(result["escalate"]) == expected_escalate, (
        f"[{case_id}] escalate {result['escalate']!r} != {expected_escalate!r}"
    )

    # Low-confidence case should additionally show a sub-0.75 score.
    if case_id == "ambiguous_low_confidence":
        assert result["confidence"] < 0.75
