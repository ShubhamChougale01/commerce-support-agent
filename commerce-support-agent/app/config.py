"""Central configuration and constants.

`load_dotenv()` is called HERE (and only here) so that importing this module
populates os.environ for the whole process. Other modules read their settings
from this module or from os.environ after importing it.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------- #
# Credentials / environment                                                    #
# --------------------------------------------------------------------------- #
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Zoho Desk (OAuth Self-Client flow)
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "")
ZOHO_ORG_ID = os.getenv("ZOHO_ORG_ID", "")
ZOHO_DC = os.getenv("ZOHO_DC", "com")  # data centre: com | in | eu | com.au
ZOHO_FROM_EMAIL = os.getenv("ZOHO_FROM_EMAIL", "")  # support mailbox for sendReply
# Medusa.js (self-hosted commerce backend)
MEDUSA_URL = os.getenv("MEDUSA_URL", "http://localhost:9000")
MEDUSA_API_KEY = os.getenv("MEDUSA_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
DESK_WEBHOOK_SECRET = os.getenv("DESK_WEBHOOK_SECRET", "")

# --------------------------------------------------------------------------- #
# Model identifiers                                                            #
# --------------------------------------------------------------------------- #
CLASSIFIER_MODEL = "claude-sonnet-4-6"
REPLY_MODEL = "claude-sonnet-4-6"
HANDOFF_MODEL = "claude-haiku-4-5-20251001"

# --------------------------------------------------------------------------- #
# Refund policy                                                               #
# --------------------------------------------------------------------------- #
AUTO_REFUND_LIMIT_INR = 2000
REFUND_WINDOW_DAYS = 15

# --------------------------------------------------------------------------- #
# Escalation routing                                                          #
# --------------------------------------------------------------------------- #
# Zoho Desk team IDs for ticket reassignment. Placeholders — swap in the real
# IDs from your portal (Setup -> Organization -> Teams, ID is in the URL) or
# set the ZOHO_TEAM_* env vars.
# `or` fallback (not getenv default) so a set-but-empty env var still yields
# the placeholder instead of silently routing to teamId="".
TEAM_FRAUD = os.getenv("ZOHO_TEAM_FRAUD") or "team_fraud_PLACEHOLDER"
TEAM_LEGAL = os.getenv("ZOHO_TEAM_LEGAL") or "team_legal_PLACEHOLDER"
TEAM_REFUNDS = os.getenv("ZOHO_TEAM_REFUNDS") or "team_refunds_PLACEHOLDER"
TEAM_GENERAL = os.getenv("ZOHO_TEAM_GENERAL") or "team_general_PLACEHOLDER"

# Internal priority tiers (1=urgent ... 4=low) -> Zoho Desk priority strings.
PRIORITY_MAP: dict[int, str] = {1: "High", 2: "High", 3: "Medium", 4: "Low"}

# Maps an escalation reason -> the human team it should land on.
#   team_id       : Zoho Desk team the ticket is reassigned to
#   priority      : internal tier (1=urgent ... 4=low); mapped to a Zoho
#                   priority string via PRIORITY_MAP at patch time
#   sla_hrs       : hours the human team has to respond
#   slack_channel : channel notified for priority-1 reasons / SLA warnings
#
# Tiers:
#   P1 (urgent, 1-2 hr)  -> fraud_flag, legal_threat
#   P2 (high,   4 hr)    -> refund_above_threshold
#   P3 (medium, 8 hr)    -> everything else
ROUTING: dict[str, dict] = {
    "fraud_flag": {
        "team_id": TEAM_FRAUD,
        "priority": 1,
        "sla_hrs": 1,
        "slack_channel": "#fraud-alerts",
    },
    "legal_threat": {
        "team_id": TEAM_LEGAL,
        "priority": 1,
        "sla_hrs": 2,
        "slack_channel": "#legal-escalations",
    },
    "refund_above_threshold": {
        "team_id": TEAM_REFUNDS,
        "priority": 2,
        "sla_hrs": 4,
        "slack_channel": "#refund-review",
    },
    "abusive_language": {
        "team_id": TEAM_GENERAL,
        "priority": 3,
        "sla_hrs": 8,
        "slack_channel": "#support-escalations",
    },
    "quality_gate_fail": {
        "team_id": TEAM_GENERAL,
        "priority": 3,
        "sla_hrs": 8,
        "slack_channel": "#support-escalations",
    },
    "low_confidence": {
        "team_id": TEAM_GENERAL,
        "priority": 3,
        "sla_hrs": 8,
        "slack_channel": "#support-escalations",
    },
    "human_requested": {
        "team_id": TEAM_GENERAL,
        "priority": 3,
        "sla_hrs": 8,
        "slack_channel": "#support-escalations",
    },
}

# Fallback route for any reason not explicitly listed in ROUTING
# (e.g. "system_error", "classifier_error", "cancel_order_requested").
DEFAULT_ROUTE: dict = {
    "team_id": TEAM_GENERAL,
    "priority": 3,
    "sla_hrs": 8,
    "slack_channel": "#support-escalations",
}


def get_route(reason: str | None) -> dict:
    """Return the routing config for an escalation reason, falling back to
    DEFAULT_ROUTE for unknown / None reasons."""
    if reason is None:
        return DEFAULT_ROUTE
    return ROUTING.get(reason, DEFAULT_ROUTE)
