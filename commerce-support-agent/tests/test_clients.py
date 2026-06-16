"""Client smoke tests against the real Medusa and Zoho Desk APIs.

Marked `integration` so they're skipped in CI with `-m "not integration"`.
Set TEST_EMAIL and TEST_TICKET_ID in the environment to point at known-good
records in your stores.
"""

import os

import pytest

from app.medusa import get_order_by_email
from app.zohodesk import get_ticket

pytestmark = pytest.mark.integration

TEST_EMAIL = os.getenv("TEST_EMAIL", "")
TEST_TICKET_ID = os.getenv("TEST_TICKET_ID", "")


@pytest.mark.asyncio
async def test_get_order_by_email():
    if not TEST_EMAIL:
        pytest.skip("TEST_EMAIL not set")
    order = await get_order_by_email(TEST_EMAIL)
    assert order is not None, "expected an order for the test email"
    assert "id" in order


@pytest.mark.asyncio
async def test_get_ticket():
    if not TEST_TICKET_ID:
        pytest.skip("TEST_TICKET_ID not set")
    ticket = await get_ticket(int(TEST_TICKET_ID))
    requester = ticket.get("requester") or {}
    # requester email is present either embedded or at the ticket level.
    assert requester.get("email") or ticket.get("requester_id"), (
        "expected requester email/id on the ticket"
    )
