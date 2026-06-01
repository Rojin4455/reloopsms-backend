import logging

import requests
from decouple import config

from core.models import AgencyToken, GHLAuthCredentials

logger = logging.getLogger(__name__)

TOKEN_REFRESH_URL = "https://services.leadconnectorhq.com/oauth/token"


def is_ghl_auth_error(response):
    """Return True when GHL rejected the request due to an expired/invalid token."""
    if response is None:
        return False
    if response.status_code == 401:
        return True
    try:
        data = response.json()
        message = str(data.get("message") or data.get("error") or "").lower()
        if "invalid jwt" in message or "token expired" in message:
            return True
    except Exception:
        pass
    return False


def refresh_location_token(credentials):
    """Refresh a single GHLAuthCredentials row. Returns True on success."""
    refresh_token = credentials.refresh_token
    if not refresh_token:
        logger.warning("Skipping GHLAuthCredentials %s: empty refresh_token", credentials.pk)
        return False

    try:
        response = requests.post(
            TOKEN_REFRESH_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": config("GHL_CLIENT_ID"),
                "client_secret": config("GHL_CLIENT_SECRET"),
                "refresh_token": refresh_token,
            },
            timeout=60,
        )
        new_tokens = response.json()
        if not response.ok or not new_tokens.get("locationId"):
            logger.error(
                "GHL location token refresh failed for %s: status=%s body=%s",
                credentials.pk,
                response.status_code,
                new_tokens,
            )
            return False

        GHLAuthCredentials.objects.update_or_create(
            location_id=new_tokens.get("locationId"),
            defaults={
                "access_token": new_tokens.get("access_token"),
                "refresh_token": new_tokens.get("refresh_token"),
                "expires_in": new_tokens.get("expires_in"),
                "scope": new_tokens.get("scope"),
                "user_type": new_tokens.get("userType"),
                "company_id": new_tokens.get("companyId"),
                "user_id": new_tokens.get("userId"),
            },
        )
        return True
    except Exception:
        logger.exception("Unexpected error refreshing location token for %s", credentials.pk)
        return False


def refresh_agency_token(credentials):
    """Refresh a single AgencyToken row. Returns True on success."""
    refresh_token = credentials.refresh_token
    if not refresh_token:
        logger.warning("Skipping AgencyToken %s: empty refresh_token", credentials.pk)
        return False

    try:
        response = requests.post(
            TOKEN_REFRESH_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": config("AGENCY_CLIENT_ID"),
                "client_secret": config("AGENCY_CLIENT_SECRET"),
                "refresh_token": refresh_token,
            },
            timeout=60,
        )
        response_data = response.json()
        if not response.ok or not response_data.get("companyId"):
            logger.error(
                "Agency token refresh failed for %s: status=%s body=%s",
                credentials.pk,
                response.status_code,
                response_data,
            )
            return False

        AgencyToken.objects.update_or_create(
            company_id=response_data.get("companyId"),
            defaults={
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "expires_in": response_data.get("expires_in"),
                "scope": response_data.get("scope"),
                "user_type": response_data.get("userType"),
                "user_id": response_data.get("userId"),
                "is_bulk_installation": response_data.get("isBulkInstallation", False),
                "token_type": response_data.get("token_type", "Bearer"),
                "refresh_token_id": response_data.get("refreshTokenId"),
            },
        )
        return True
    except Exception:
        logger.exception("Unexpected error refreshing agency token for %s", credentials.pk)
        return False


def refresh_all_ghl_tokens():
    """Refresh every stored location and agency OAuth token."""
    location_ok = sum(refresh_location_token(creds) for creds in GHLAuthCredentials.objects.all())
    agency_ok = sum(refresh_agency_token(creds) for creds in AgencyToken.objects.all())
    logger.info("Refreshed GHL tokens: %s location(s), %s agency", location_ok, agency_ok)
    return location_ok, agency_ok


def ghl_request(method, url, *, headers=None, auth_credentials=None, retry_on_auth=True, timeout=60, **kwargs):
    """
    Make a GHL API request. On 401 / Invalid JWT, refresh all tokens and retry once.

    auth_credentials: GHLAuthCredentials instance tied to the Bearer token (used to
                      reload access_token after refresh).
    """
    headers = dict(headers or {})
    response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)

    if not retry_on_auth or not is_ghl_auth_error(response):
        return response

    logger.warning(
        "GHL auth error on %s %s (status=%s) — refreshing all tokens and retrying once",
        method,
        url,
        response.status_code,
    )
    refresh_all_ghl_tokens()

    if auth_credentials is not None:
        auth_credentials.refresh_from_db()
        headers["Authorization"] = f"Bearer {auth_credentials.access_token}"

    return requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
