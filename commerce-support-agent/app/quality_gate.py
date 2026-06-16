"""Quality gate — the last check before any reply reaches a customer.

Scans the generated reply for leaked PII, excessive length, and policy
violations (competitor mentions, overpromises). A failure routes the ticket to
a human instead of sending the draft.
"""

import re
from dataclasses import dataclass


@dataclass
class QualityGateResult:
    passed: bool
    reason: str | None


# --------------------------------------------------------------------------- #
# PII patterns                                                                 #
# --------------------------------------------------------------------------- #
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Indian mobile: optional +91/91 prefix, then a 10-digit number starting 6-9.
# Word boundaries keep it from matching inside longer digit runs (e.g. Aadhaar).
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\-\s]?)?[6-9]\d{9}(?!\d)")

# Aadhaar: 12 consecutive digits (optionally spaced in 4-4-4 groups).
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")

# PAN: 5 uppercase letters, 4 digits, 1 uppercase letter.
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")

# UPI handle: text@text (e.g. name@okhdfc). Excludes patterns with a dot in the
# domain part so genuine emails are caught by _EMAIL_RE, not double-flagged.
_UPI_RE = re.compile(r"\b[a-zA-Z0-9.\-]{2,}@[a-zA-Z]{2,}\b")

# Credit-card last-4 mention, e.g. "card ending 1234" / "card ending in 4321".
_CARD_LAST4_RE = re.compile(
    r"(?:card|ending|xxxx|x{4})[\s\-]*(?:in\s*)?\d{4}\b", re.IGNORECASE
)

# Ordered so the most specific / informative reason wins.
_PII_CHECKS: list[tuple[str, re.Pattern]] = [
    ("pii_aadhaar", _AADHAAR_RE),
    ("pii_pan", _PAN_RE),
    ("pii_card", _CARD_LAST4_RE),
    ("pii_phone", _PHONE_RE),
    ("pii_email", _EMAIL_RE),
    ("pii_upi", _UPI_RE),
]

# --------------------------------------------------------------------------- #
# Policy lists                                                                 #
# --------------------------------------------------------------------------- #
# Clients populate this with competitor brand names to block.
COMPETITOR_BRANDS: list[str] = []

OVERPROMISE_PHRASES: list[str] = [
    "i guarantee",
    "i promise",
    "definitely will",
    "100%",
]

MAX_WORDS = 300


def check_reply(reply_text: str, brand_config) -> QualityGateResult:
    """Run all gate checks. Returns the first failure, or a pass."""
    text = reply_text or ""

    # 1. PII scan (run email before UPI so a real email isn't tagged as UPI).
    for reason, pattern in _PII_CHECKS:
        if pattern.search(text):
            # The UPI regex also matches emails; if the only hit is an email,
            # report it as pii_email (handled by ordering: email check precedes
            # nothing relevant here, so guard explicitly).
            if reason == "pii_upi" and _EMAIL_RE.search(text):
                continue
            return QualityGateResult(passed=False, reason=reason)

    # 2. Length.
    if len(text.split()) > MAX_WORDS:
        return QualityGateResult(passed=False, reason="reply_too_long")

    lowered = text.lower()

    # 3a. Competitor mentions.
    for brand in COMPETITOR_BRANDS:
        if brand.lower() in lowered:
            return QualityGateResult(passed=False, reason="competitor_mention")

    # 3b. Overpromises.
    for phrase in OVERPROMISE_PHRASES:
        if phrase in lowered:
            return QualityGateResult(passed=False, reason="overpromise_detected")

    return QualityGateResult(passed=True, reason=None)
