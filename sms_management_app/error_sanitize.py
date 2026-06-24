"""
Strip secrets and Celery retry noise from error strings before storage or API output.
"""

import re

_GHL_TOKEN_KWARG = re.compile(
    r"(['\"]ghl_token['\"]\s*:\s*['\"])[^'\"]*(['\"])",
    re.IGNORECASE,
)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_CELERY_GHL_RETRY = re.compile(
    r"Can't retry\s+sms_management_app\.tasks\.update_ghl_message_status_task",
    re.IGNORECASE,
)


def sanitize_error_text(value, *, placeholder_for_polluted=False):
    """
    Redact tokens and normalize known polluted GHL-sync Celery messages.

    When ``placeholder_for_polluted`` is True and the whole string is a known
    GHL-sync pollution pattern, return a short user-facing placeholder instead.
    """
    if not value:
        return value

    text = str(value)

    if placeholder_for_polluted and _is_polluted_ghl_sync_message(text):
        return "GHL status sync failed (details redacted)"

    text = _GHL_TOKEN_KWARG.sub(r"\1[redacted]\2", text)
    text = _JWT.sub("[redacted token]", text)

    if _CELERY_GHL_RETRY.search(text):
        return "GHL status sync failed after retries (details redacted)"

    if text.strip().lower().startswith("ghl update failed"):
        return "GHL status sync failed (details redacted)"

    return text


def _is_polluted_ghl_sync_message(text):
    lower = text.lower()
    return (
        "can't retry" in lower
        or "ghl update failed" in lower
        or "ghl_token" in lower
        or _JWT.search(text) is not None
    )
