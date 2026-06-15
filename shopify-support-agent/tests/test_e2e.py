"""End-to-end pipeline tests with all external calls mocked.

These do NOT hit any live API (no `integration` marker), so they run in CI with
`-m "not integration"`. They assert the orchestration decisions in run_agent:
which Zoho Desk/Medusa calls fire for each ticket shape.
"""

import pytest

import app.agent as agent


@pytest.fixture
def harness(monkeypatch):
    """Patch every external dependency of run_agent and record the calls."""
    events = []

    async def fake_get_ticket(tid):
        return {
            "id": tid,
            "requester": {"email": "cust@example.com", "name": "Cust X"},
            "description_text": "where is my order",
            "subject": "Order #1234",
        }

    async def fake_thread(tid):
        return []

    async def fake_order_by_id(oid):
        return {
            "order_number": 1234,
            "total_price": "500",
            "fulfillment_status": "fulfilled",
        }

    async def fake_order_by_email(email):
        return None

    async def fake_issue_refund(oid, amount, reason):
        events.append(("refund", amount))
        return {"id": "r1"}

    async def fake_post_reply(tid, body):
        events.append(("reply", tid))

    async def fake_escalate(t, c, od, bc, reason=None, draft=None):
        events.append(("escalate", reason))

    def fake_log(**kw):
        events.append(("log", kw["classification"].get("intent")))

    monkeypatch.setattr(agent, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(agent, "get_ticket_thread", fake_thread)
    monkeypatch.setattr(agent, "get_order_by_id", fake_order_by_id)
    monkeypatch.setattr(agent, "get_order_by_email", fake_order_by_email)
    monkeypatch.setattr(agent, "issue_refund", fake_issue_refund)
    monkeypatch.setattr(agent, "post_reply", fake_post_reply)
    monkeypatch.setattr(agent, "escalate_to_human", fake_escalate)
    monkeypatch.setattr(agent, "_log_processed", fake_log)

    def set_classification(d):
        async def f(body, order, history=None, **kwargs):
            return d

        monkeypatch.setattr(agent, "classify_ticket", f)

    def set_reply(text):
        async def f(*args):
            return text

        monkeypatch.setattr(agent, "generate_reply", f)

    return events, set_classification, set_reply


def _cls(**over):
    base = {
        "intent": "order_status",
        "confidence": 0.9,
        "sentiment": "neutral",
        "fraud_flags": {"triggered": False, "reasons": []},
        "refund_amount_inr": None,
        "escalate": False,
        "escalation_reason": None,
        "shopify_action": None,
    }
    base.update(over)
    return base


def test_extract_order_id():
    assert agent._extract_order_id("Issue with #1234", "body") == "1234"
    assert agent._extract_order_id("no num", "see order #99") == "99"
    assert agent._extract_order_id("nothing", "nothing") is None


@pytest.mark.asyncio
async def test_happy_path_sends_reply(harness):
    events, set_classification, set_reply = harness
    set_classification(_cls(intent="order_status"))
    set_reply("Hi Cust! Your order is on the way. Warm regards, Team")
    await agent.run_agent(1)
    assert ("reply", 1) in events
    assert ("log", "order_status") in events
    assert not any(e[0] == "escalate" for e in events)


@pytest.mark.asyncio
async def test_hard_escalation(harness):
    events, set_classification, _ = harness
    set_classification(_cls(escalate=True, escalation_reason="legal_threat"))
    await agent.run_agent(2)
    assert events == [("escalate", "legal_threat")]


@pytest.mark.asyncio
async def test_fraud_triggers_escalation(harness):
    events, set_classification, _ = harness
    set_classification(_cls(fraud_flags={"triggered": True, "reasons": ["x"]}))
    await agent.run_agent(3)
    assert events == [("escalate", "fraud_flag")]


@pytest.mark.asyncio
async def test_refund_under_limit_auto_issues(harness):
    events, set_classification, set_reply = harness
    set_classification(
        _cls(intent="refund_request", shopify_action="issue_refund", refund_amount_inr=850)
    )
    set_reply("Your refund is done. Warm regards, Team")
    await agent.run_agent(4)
    assert ("refund", 850.0) in events
    assert ("reply", 4) in events


@pytest.mark.asyncio
async def test_refund_over_limit_escalates(harness):
    events, set_classification, _ = harness
    set_classification(
        _cls(intent="refund_request", shopify_action="issue_refund", refund_amount_inr=5000)
    )
    await agent.run_agent(5)
    assert events == [("escalate", "refund_above_threshold")]


@pytest.mark.asyncio
async def test_cancel_escalates(harness):
    events, set_classification, _ = harness
    set_classification(_cls(intent="cancellation", shopify_action="cancel_order"))
    await agent.run_agent(6)
    assert events == [("escalate", "human_requested")]


@pytest.mark.asyncio
async def test_quality_gate_fail_escalates(harness):
    events, set_classification, set_reply = harness
    set_classification(_cls(intent="order_status"))
    set_reply("contact me at leak@pii.com")  # PII -> gate fail
    await agent.run_agent(7)
    assert events == [("escalate", "quality_gate_fail")]


@pytest.mark.asyncio
async def test_system_error_escalates(harness, monkeypatch):
    events, _set_classification, _ = harness

    async def boom(body, order, history=None, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(agent, "classify_ticket", boom)
    await agent.run_agent(8)
    assert events == [("escalate", "system_error")]
