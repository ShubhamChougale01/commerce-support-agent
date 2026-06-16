"""Async Medusa.js (v2) Admin API client.

Drop-in replacement for the old commerce client: exposes the SAME five function
signatures and normalizes Medusa responses into the order shape the rest of
the codebase consumes:

    { "order_number", "name", "email", "total_price", "fulfillment_status",
      "created_at",
      "fulfillments": [{"shipment_status", "tracking_numbers",
                        "tracking_urls", "estimated_delivery_at"}] }

Auth: a Medusa secret API key sent as HTTP Basic (key as username, empty
password). All calls retry 3x with exponential backoff via tenacity.

NOTE (D4): Medusa v2 amounts are major units (e.g. 850 == ₹850) — no paise
conversion. Verify against your local instance if you customised currencies.
"""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import MEDUSA_API_KEY, MEDUSA_URL

BASE_URL = f"{MEDUSA_URL.rstrip('/')}/admin" if MEDUSA_URL else "/admin"

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        auth=(MEDUSA_API_KEY, ""),  # secret api key as Basic username
        headers={"Content-Type": "application/json"},
        timeout=15.0,
    )


# Fields needed to build the normalized shape + locate payments for refunds.
_ORDER_FIELDS = (
    "id,display_id,email,total,currency_code,status,fulfillment_status,"
    "payment_status,created_at,"
    "*fulfillments,*fulfillments.labels,"
    "*payment_collections,*payment_collections.payments"
)


def _normalize_order(raw: dict) -> dict:
    """Map a Medusa v2 order onto the shape downstream code expects."""
    fulfillments = []
    for f in raw.get("fulfillments") or []:
        labels = f.get("labels") or []
        # Derive a shipment status from the fulfillment lifecycle timestamps.
        if f.get("delivered_at"):
            status = "delivered"
        elif f.get("shipped_at"):
            status = "in_transit"
        elif f.get("canceled_at"):
            status = "canceled"
        else:
            status = "pending"
        fulfillments.append(
            {
                "shipment_status": status,
                "tracking_numbers": [
                    l["tracking_number"] for l in labels if l.get("tracking_number")
                ],
                "tracking_urls": [
                    l["tracking_url"] for l in labels if l.get("tracking_url")
                ],
                "estimated_delivery_at": f.get("delivered_at"),
            }
        )

    display_id = raw.get("display_id")
    return {
        "id": raw.get("id"),
        "order_number": display_id,
        "name": f"#{display_id}" if display_id is not None else None,
        "email": raw.get("email"),
        "total_price": str(raw.get("total", "")),
        "currency": raw.get("currency_code"),
        "fulfillment_status": raw.get("fulfillment_status") or "unfulfilled",
        "created_at": raw.get("created_at"),
        "fulfillments": fulfillments,
        # Kept for issue_refund(); harmless extra context for the classifier.
        "payment_collections": raw.get("payment_collections") or [],
    }


@_retry
async def get_order_by_email(email: str) -> dict | None:
    """Return the most recent order for an email, or None if none exist."""
    async with _client() as client:
        resp = await client.get(
            "/orders",
            params={"q": email, "limit": 5, "fields": _ORDER_FIELDS},
        )
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
    # `q` is a fuzzy search — keep only exact email matches.
    orders = [o for o in orders if (o.get("email") or "").lower() == email.lower()]
    if not orders:
        return None
    orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return _normalize_order(orders[0])


@_retry
async def get_order_by_id(order_id: str) -> dict | None:
    """Return a single order by Medusa ID or display_id, or None if missing."""
    async with _client() as client:
        # The agent extracts numeric display ids (#42) from ticket text; Medusa
        # primary ids look like "order_01H...". Resolve display ids via list.
        if str(order_id).isdigit():
            # Medusa's admin /orders endpoint silently ignores a `display_id`
            # filter param (verified against v2: it returns ALL orders), so we
            # page newest-first and match client-side — same approach as
            # get_order_by_email. limit=100 covers recent orders for this use
            # case; a high-volume store would need full pagination here.
            resp = await client.get(
                "/orders",
                params={
                    "order": "-display_id",
                    "limit": 100,
                    "fields": _ORDER_FIELDS,
                },
            )
            resp.raise_for_status()
            orders = resp.json().get("orders", [])
            target = int(order_id)
            match = next(
                (o for o in orders if o.get("display_id") == target), None
            )
            return _normalize_order(match) if match else None

        resp = await client.get(
            f"/orders/{order_id}", params={"fields": _ORDER_FIELDS}
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        order = resp.json().get("order")
        return _normalize_order(order) if order else None


@_retry
async def get_fulfillment_status(order_id: str) -> dict:
    """Return a flat fulfillment summary for an order."""
    order = await get_order_by_id(order_id)
    fulfillments = (order or {}).get("fulfillments") or []
    if not fulfillments:
        return {
            "status": "unfulfilled",
            "tracking_number": None,
            "tracking_url": None,
            "estimated_delivery": None,
        }
    latest = fulfillments[-1]
    numbers = latest.get("tracking_numbers") or []
    urls = latest.get("tracking_urls") or []
    return {
        "status": latest.get("shipment_status"),
        "tracking_number": numbers[0] if numbers else None,
        "tracking_url": urls[0] if urls else None,
        "estimated_delivery": latest.get("estimated_delivery_at"),
    }


@_retry
async def issue_refund(order_id: str, amount_inr: float, reason: str) -> dict:
    """Refund against an order's captured payment (two-step in Medusa).

    1. Resolve the order and find a captured payment in its payment
       collections.
    2. POST /admin/payments/{payment_id}/refund with the amount.
    """
    order = await get_order_by_id(order_id)
    if order is None:
        raise ValueError(f"order {order_id!r} not found, cannot refund")

    payment_id = None
    for collection in order.get("payment_collections") or []:
        for payment in collection.get("payments") or []:
            if payment.get("captured_at") and not payment.get("canceled_at"):
                payment_id = payment.get("id")
                break
        if payment_id:
            break
    if payment_id is None:
        raise ValueError(f"no captured payment on order {order_id!r}")

    async with _client() as client:
        resp = await client.post(
            f"/payments/{payment_id}/refund",
            json={"amount": amount_inr, "note": reason},
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("payment", body)


@_retry
async def get_customer_order_count(email: str) -> int:
    """Total orders placed by an email (fraud-detection signal)."""
    async with _client() as client:
        resp = await client.get(
            "/orders",
            params={"q": email, "limit": 250, "fields": "id,email"},
        )
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
    return sum(1 for o in orders if (o.get("email") or "").lower() == email.lower())
