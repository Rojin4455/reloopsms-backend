"""
Central failure categorization for SMS messages.

Patterns here are derived from real production failures (see the exported
failed-messages CSV). Keeping this in one place means the send path, DLR
callback, retry endpoints and the data backfill all agree on the category.
"""

# Category constants (stored in SMSMessage.error_category)
OPT_OUT = "opt_out"
INVALID_RECIPIENT = "invalid_recipient"
PROVIDER_BILLING = "provider_billing"   # agency/wholesale TransmitSMS credit (LEDGER_ERROR)
RATE_LIMITED = "rate_limited"           # 429 from TransmitSMS
AUTH_ERROR = "auth_error"               # invalid/expired token, 401
PROVIDER_DOWN = "provider_down"         # 5xx / maintenance
CONFIG_ERROR = "config_error"           # misconfiguration (MMS key, bad sender)
UNKNOWN = "unknown"

# Human-friendly labels (used by the API/UI)
CATEGORY_LABELS = {
    OPT_OUT: "Opt-out",
    INVALID_RECIPIENT: "Invalid recipient",
    PROVIDER_BILLING: "Provider credit",
    RATE_LIMITED: "Rate limited",
    AUTH_ERROR: "Auth error",
    PROVIDER_DOWN: "Provider down",
    CONFIG_ERROR: "Config error",
    UNKNOWN: "Unknown",
}

ALL_CATEGORIES = list(CATEGORY_LABELS.keys())

# Categories where retrying the SAME message will always fail until something
# external changes (the contact opts back in, the number is corrected, the token
# is refreshed, the config is fixed). These are excluded from bulk retry by default.
PERMANENT_CATEGORIES = {OPT_OUT, INVALID_RECIPIENT, AUTH_ERROR, CONFIG_ERROR}


def categorize_failure(error_message="", error_code=None):
    """
    Map a TransmitSMS/GHL error string (and optional error_code) to a category.
    Order matters: more specific checks come first.
    """
    text = f"{error_code or ''} {error_message or ''}".lower()

    if not (error_message or "").strip() and not error_code:
        return UNKNOWN

    # Opt-out must be checked before generic recipient errors, since opt-out
    # failures are reported as RECIPIENTS_ERROR with an "optout" payload.
    if "optout" in text or "opted-out" in text or "opt-out" in text:
        return OPT_OUT

    if "recipients_error" in text or "recipient error" in text or (
        "invalid" in text and ("recipient" in text or "number" in text)
    ):
        return INVALID_RECIPIENT

    if "ledger_error" in text or "insufficient funds" in text:
        return PROVIDER_BILLING

    if "429" in text or "too many requests" in text or "rate limit" in text:
        return RATE_LIMITED

    if "invalid jwt" in text or "401" in text or "unauthorized" in text or "invalid_token" in text:
        return AUTH_ERROR

    if (
        "maintainence" in text or "maintenance" in text
        or "is down" in text or "503" in text or "502" in text or "500 server" in text
    ):
        return PROVIDER_DOWN

    if "provider key is required" in text or "bad_caller_id" in text or "mms provider" in text:
        return CONFIG_ERROR

    return UNKNOWN


def is_retryable_category(category):
    """True if a message in this category is worth retrying (in bulk)."""
    return category not in PERMANENT_CATEGORIES


def category_label(category):
    return CATEGORY_LABELS.get(category, CATEGORY_LABELS[UNKNOWN])
