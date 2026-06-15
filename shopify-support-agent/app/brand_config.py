"""Per-client brand configuration.

For now every client resolves to DEFAULT_BRAND_CONFIG. The lookup is centralised
in get_brand_config() so that swapping in a Supabase-backed implementation later
is a one-function change.
"""

from dataclasses import dataclass, field

from app.config import AUTO_REFUND_LIMIT_INR


@dataclass(frozen=True)
class BrandConfig:
    client_id: str
    brand_name: str
    voice: str
    tone_example: str
    refund_days: int
    exchange_days: int
    non_refundable: list[str]
    auto_refund_limit: int
    sign_off: str


DEFAULT_BRAND_CONFIG = BrandConfig(
    client_id="default",
    brand_name="Our Store",
    voice=(
        "Warm, concise, and helpful. Speaks like a knowledgeable friend — "
        "human, never robotic or corporate. Confident but humble."
    ),
    tone_example=(
        "Hi Priya! Totally understand wanting your order to arrive soon — "
        "I just checked and it's out for delivery, you should have it by "
        "tomorrow evening. Hang tight and reach out if anything looks off!"
    ),
    refund_days=15,
    exchange_days=30,
    non_refundable=["innerwear", "final sale items", "gift cards", "opened cosmetics"],
    auto_refund_limit=AUTO_REFUND_LIMIT_INR,
    sign_off="Warm regards,\nThe Customer Care Team",
)


def get_brand_config(client_id: str) -> BrandConfig:
    """Return the brand configuration for a client.

    Currently returns DEFAULT_BRAND_CONFIG for every client_id.
    """
    # TODO: fetch from Supabase brands table by client_id
    return DEFAULT_BRAND_CONFIG
