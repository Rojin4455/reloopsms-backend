import json
import logging
from urllib.parse import urlencode

import requests
from decouple import config
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.models import AgencyToken, CompanyToken, GHLAuthCredentials

logger = logging.getLogger(__name__)

TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"
GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"
# White-label installs: use leadconnectorhq.com v2 (override via GHL_OAUTH_AUTHORIZE_URL if needed).
DEFAULT_LOCATION_OAUTH_AUTHORIZE_URL = (
    "https://marketplace.leadconnectorhq.com/v2/oauth/chooselocation"
)
DEFAULT_AGENCY_OAUTH_AUTHORIZE_URL = (
    "https://marketplace.leadconnectorhq.com/v2/oauth/chooselocation"
)


def _sanitize_oauth_payload(payload: dict) -> dict:
    """Mask tokens for safe logging."""
    sanitized = dict(payload or {})
    for key in ("access_token", "refresh_token"):
        value = sanitized.get(key)
        if isinstance(value, str) and value:
            sanitized[key] = f"{value[:12]}...({len(value)} chars)"
    return sanitized


def _debug_ghl_oauth(step: str, payload) -> None:
    message = json.dumps(payload, default=str) if not isinstance(payload, str) else payload
    print(f"[GHL OAuth] {step}: {message}")
    logger.info("[GHL OAuth] %s: %s", step, message)


def parse_location_ids_from_query_params(query_params) -> list[str]:
    """Collect one or many location IDs from GHL OAuth callback query params."""
    location_ids: list[str] = []
    multi_keys = (
        "locationId",
        "location_id",
        "locid",
        "selectedLocationId",
    )
    list_keys = ("locationIds", "location_ids", "selectedLocationIds")

    getlist = getattr(query_params, "getlist", None)
    if getlist:
        for key in multi_keys:
            for value in getlist(key):
                value = (value or "").strip()
                if value and value not in location_ids:
                    location_ids.append(value)
        for key in list_keys:
            raw = query_params.get(key)
            if not raw:
                continue
            for part in str(raw).split(","):
                part = part.strip()
                if part and part not in location_ids:
                    location_ids.append(part)
        return location_ids

    for key in multi_keys:
        value = (query_params.get(key) or "").strip()
        if value and value not in location_ids:
            location_ids.append(value)
    for key in list_keys:
        raw = query_params.get(key)
        if not raw:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if part and part not in location_ids:
                location_ids.append(part)
    return location_ids


def _get_app_id(client_id: str | None = None) -> str:
    """Marketplace app id (appId param on installedLocations, etc.)."""
    explicit = config("GHL_APP_ID", default="")
    if explicit:
        return explicit
    resolved = client_id or config("GHL_CLIENT_ID", default="")
    if resolved and "-" in resolved:
        return resolved.split("-", 1)[0]
    return resolved


def _get_version_id(client_id: str | None = None) -> str:
    """OAuth authorize URL version_id (published app version, not the app id)."""
    explicit = config("VERSION_ID", default="") or config("GHL_VERSION_ID", default="")
    if explicit:
        return explicit
    return _get_app_id(client_id)


def _get_agency_app_id() -> str:
    explicit = config("GHL_AGENCY_APP_ID", default="")
    if explicit:
        return explicit
    client_id = config("AGENCY_CLIENT_ID", default="")
    if client_id and "-" in client_id:
        return client_id.split("-", 1)[0]
    return client_id


def _post_token(payload: dict) -> tuple[dict | None, str | None]:
    _debug_ghl_oauth(
        "oauth/token request",
        {
            "grant_type": payload.get("grant_type"),
            "user_type": payload.get("user_type"),
            "client_id": payload.get("client_id"),
            "redirect_uri": payload.get("redirect_uri"),
            "has_code": bool(payload.get("code")),
            "has_refresh_token": bool(payload.get("refresh_token")),
        },
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        response = requests.post(TOKEN_URL, headers=headers, data=payload, timeout=60)
    except requests.RequestException as exc:
        logger.exception("GHL oauth/token request failed")
        return None, str(exc)

    _debug_ghl_oauth(
        "oauth/token response meta",
        {"status_code": response.status_code, "body_preview": response.text[:500]},
    )

    try:
        response_data = response.json()
    except requests.exceptions.JSONDecodeError:
        return None, f"Invalid JSON from GHL token endpoint (status={response.status_code})"

    _debug_ghl_oauth("oauth/token response data", _sanitize_oauth_payload(response_data))

    if not response.ok or not response_data.get("access_token"):
        detail = response_data.get("error_description") or response_data.get("error") or response_data
        return None, str(detail)

    return response_data, None


def _build_oauth_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    scope: str,
    version_id: str,
    authorize_url: str,
) -> str:
    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": scope,
        "loginWindowOpenMode": "self",
    }
    if version_id:
        params["version_id"] = version_id
    return f"{authorize_url}?{urlencode(params)}"


def build_location_oauth_url():
    """Build the GHL location OAuth authorize URL (Marketplace v2 + version_id)."""
    client_id = config("GHL_CLIENT_ID")
    authorize_url = config(
        "GHL_OAUTH_AUTHORIZE_URL",
        default=DEFAULT_LOCATION_OAUTH_AUTHORIZE_URL,
    )
    return _build_oauth_authorize_url(
        client_id=client_id,
        redirect_uri=config("GHL_REDIRECTED_URI"),
        scope=config("SCOPE"),
        version_id=_get_version_id(client_id),
        authorize_url=authorize_url,
    )


def build_agency_oauth_url():
    """Build the GHL agency OAuth authorize URL (Marketplace v2 + version_id)."""
    client_id = config("AGENCY_CLIENT_ID")
    authorize_url = config(
        "GHL_AGENCY_OAUTH_AUTHORIZE_URL",
        default=DEFAULT_AGENCY_OAUTH_AUTHORIZE_URL,
    )
    return _build_oauth_authorize_url(
        client_id=client_id,
        redirect_uri=config("AGENCY_REDIRECT_URI"),
        scope=config("AGENCY_SCOPE"),
        version_id=_get_agency_app_id(),
        authorize_url=authorize_url,
    )


def _location_id_from_record(record: dict) -> str | None:
    return record.get("locationId") or record.get("_id") or record.get("id")


def _list_installed_locations(company_access_token: str, company_id: str) -> list[dict]:
    url = f"{GHL_API_BASE}/oauth/installedLocations"
    app_id = _get_app_id()
    headers = {
        "Authorization": f"Bearer {company_access_token}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
    }
    all_locations: list[dict] = []
    skip = 0
    page_limit = 100
    while True:
        response = requests.get(
            url,
            headers=headers,
            params={
                "companyId": company_id,
                "appId": app_id,
                "isInstalled": "true",
                "skip": str(skip),
                "limit": str(page_limit),
            },
            timeout=60,
        )
        if not response.ok:
            logger.warning(
                "installedLocations failed (companyId=%s appId=%s): %s",
                company_id,
                app_id,
                response.text[:300],
            )
            break
        payload = response.json()
        _debug_ghl_oauth(
            "installedLocations page",
            {
                "skip": skip,
                "status_code": response.status_code,
                "payload_keys": list(payload.keys()),
                "batch_size": len(payload.get("locations") or payload.get("data") or []),
            },
        )
        batch = payload.get("locations") or payload.get("data") or []
        if isinstance(batch, dict):
            batch = batch.get("locations", [])
        if not batch:
            break
        for loc in batch:
            if not _location_id_from_record(loc):
                continue
            if loc.get("isInstalled") is False:
                continue
            all_locations.append(loc)
        if len(batch) < page_limit:
            break
        skip += page_limit
    _debug_ghl_oauth(
        "installedLocations resolved",
        {
            "company_id": company_id,
            "count": len(all_locations),
            "location_ids": [_location_id_from_record(loc) for loc in all_locations],
        },
    )
    return all_locations


def _resolve_installed_locations(
    company_access_token: str,
    company_id: str,
    preferred_location_ids: list[str] | None = None,
) -> list[dict]:
    if preferred_location_ids:
        return [{"locationId": location_id} for location_id in preferred_location_ids]
    return _list_installed_locations(company_access_token, company_id)


def _mint_location_token(company_access_token: str, company_id: str, location_id: str) -> dict:
    url = f"{GHL_API_BASE}/oauth/locationToken"
    headers = {
        "Authorization": f"Bearer {company_access_token}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = requests.post(
        url,
        headers=headers,
        json={"companyId": company_id, "locationId": location_id},
        timeout=60,
    )
    _debug_ghl_oauth(
        "locationToken response meta",
        {
            "location_id": location_id,
            "company_id": company_id,
            "status_code": response.status_code,
            "body_preview": response.text[:300],
        },
    )
    if not response.ok:
        raise ValueError(f"locationToken failed: {response.text[:300]}")
    token_data = response.json()
    _debug_ghl_oauth("locationToken response data", _sanitize_oauth_payload(token_data))
    return token_data


def _try_mint_location_token(
    company_access_token: str, company_id: str, location_id: str
) -> dict | None:
    try:
        return _mint_location_token(company_access_token, company_id, location_id)
    except ValueError as exc:
        if "not active" in str(exc).lower():
            logger.warning("Skipping inactive location %s: %s", location_id, exc)
            return None
        raise


def _clip(value, max_length: int):
    if value is None:
        return value
    text = str(value)
    if len(text) > max_length:
        logger.warning("Truncating OAuth field from %s to %s characters", len(text), max_length)
        return text[:max_length]
    return text


def _save_company_token_from_location_app(response_data: dict) -> CompanyToken:
    company_id = response_data["companyId"]
    obj, _created = CompanyToken.objects.update_or_create(
        company_id=company_id,
        defaults={
            "access_token": response_data.get("access_token"),
            "refresh_token": response_data.get("refresh_token"),
            "expires_in": response_data.get("expires_in"),
            "scope": response_data.get("scope"),
            "user_type": _clip(response_data.get("userType"), 50),
            "user_id": _clip(response_data.get("userId"), 128),
            "is_bulk_installation": response_data.get("isBulkInstallation", False),
            "token_type": _clip(response_data.get("token_type", "Bearer"), 50),
            "refresh_token_id": _clip(response_data.get("refreshTokenId"), 128),
        },
    )
    return obj


def _upsert_location_credentials(location_token_data: dict):
    from core.services import get_location_name

    location_id = location_token_data.get("locationId")
    if not location_id:
        raise ValueError("Location token response missing locationId")

    location_data = {}
    try:
        data = get_location_name(
            location_id=location_id,
            access_token=location_token_data.get("access_token"),
        )
        location_data = (data or {}).get("location") or {}
    except Exception:
        logger.exception("Could not fetch location details for %s during OAuth exchange", location_id)

    return GHLAuthCredentials.objects.update_or_create(
        location_id=location_id,
        defaults={
            "access_token": location_token_data.get("access_token"),
            "refresh_token": location_token_data.get("refresh_token"),
            "expires_in": location_token_data.get("expires_in"),
            "scope": location_token_data.get("scope"),
            "user_type": _clip(location_token_data.get("userType"), 50),
            "company_id": _clip(location_token_data.get("companyId"), 255),
            "user_id": _clip(location_token_data.get("userId") or "", 255),
            "location_name": _clip(location_data.get("name"), 255),
            "timezone": _clip(location_data.get("timezone"), 100),
            "business_email": location_data.get("email"),
            "business_phone": _clip(location_data.get("phone"), 20),
        },
    )


def _connect_locations_from_records(
    company_access_token: str,
    company_id: str,
    location_records: list[dict],
) -> tuple[list[tuple[GHLAuthCredentials, bool]], list[dict]]:
    """Mint locationToken + upsert GHLAuthCredentials for each installed location record."""
    connected: list[tuple[GHLAuthCredentials, bool]] = []
    skipped_locations: list[dict] = []

    for record in location_records:
        loc_id = _location_id_from_record(record)
        if not loc_id:
            continue
        try:
            loc_token = _try_mint_location_token(company_access_token, company_id, loc_id)
        except ValueError as exc:
            skipped_locations.append(
                {
                    "location_id": loc_id,
                    "name": record.get("name"),
                    "reason": str(exc),
                }
            )
            continue
        if not loc_token:
            skipped_locations.append(
                {
                    "location_id": loc_id,
                    "name": record.get("name"),
                    "reason": "inactive",
                }
            )
            continue
        obj, created = _upsert_location_credentials(loc_token)
        connected.append((obj, created))

    return connected, skipped_locations


def remint_installed_location_tokens_for_company(
    company_id: str,
    *,
    refresh_company_token_first: bool = True,
) -> dict:
    """
    Re-mint v2 location tokens for every GHL-installed sub-account under a company.

    Uses the stored CompanyToken to call installedLocations + locationToken, then
    upserts GHLAuthCredentials. Intended for v1 -> v2 token upgrades without a
    full browser OAuth round-trip.
    """
    try:
        company_token = CompanyToken.objects.get(company_id=company_id)
    except CompanyToken.DoesNotExist:
        raise ValueError(f"No CompanyToken found for company_id={company_id}")

    company_access_token = company_token.access_token
    if refresh_company_token_first:
        refreshed = _refresh_company_token_with_client(
            company_token.refresh_token,
            client_id=config("GHL_CLIENT_ID"),
            client_secret=config("GHL_CLIENT_SECRET"),
            redirect_uri=config("GHL_REDIRECTED_URI"),
        )
        if not refreshed:
            raise ValueError(f"Company token refresh failed for company_id={company_id}")
        CompanyToken.objects.update_or_create(
            company_id=company_id,
            defaults={
                "access_token": refreshed.get("access_token"),
                "refresh_token": refreshed.get("refresh_token"),
                "expires_in": refreshed.get("expires_in"),
                "scope": refreshed.get("scope"),
                "user_type": _clip(refreshed.get("userType"), 50),
                "user_id": _clip(refreshed.get("userId"), 128),
                "is_bulk_installation": refreshed.get("isBulkInstallation", False),
                "token_type": _clip(refreshed.get("token_type", "Bearer"), 50),
                "refresh_token_id": _clip(refreshed.get("refreshTokenId"), 128),
            },
        )
        company_access_token = refreshed["access_token"]

    installed = _list_installed_locations(company_access_token, company_id)
    connected, skipped = _connect_locations_from_records(
        company_access_token,
        company_id,
        installed,
    )
    return {
        "company_id": company_id,
        "installed_count": len(installed),
        "connected_count": len(connected),
        "created_count": sum(1 for _, created in connected if created),
        "updated_count": sum(1 for _, created in connected if not created),
        "connected_location_ids": [obj.location_id for obj, _ in connected],
        "skipped_locations": skipped,
    }


def _connect_company_install(
    response_data: dict,
    preferred_location_ids: list[str] | None = None,
) -> tuple[dict | None, str | None]:
    company_id = response_data.get("companyId")
    if not company_id:
        return None, "OAuth response missing companyId"

    _debug_ghl_oauth(
        "company install start",
        {
            "company_id": company_id,
            "user_type": response_data.get("userType"),
            "is_bulk_installation": response_data.get("isBulkInstallation"),
            "preferred_location_ids": preferred_location_ids or [],
            "token_location_id": response_data.get("locationId"),
        },
    )

    _save_company_token_from_location_app(response_data)
    company_access_token = response_data["access_token"]

    if preferred_location_ids:
        location_records = _resolve_installed_locations(
            company_access_token,
            company_id,
            preferred_location_ids=preferred_location_ids,
        )
    else:
        installed = _list_installed_locations(company_access_token, company_id)
        existing_ids = set(
            GHLAuthCredentials.objects.filter(company_id=company_id).values_list(
                "location_id", flat=True
            )
        )
        new_location_ids = [
            _location_id_from_record(loc)
            for loc in installed
            if _location_id_from_record(loc) not in existing_ids
        ]
        # Re-auth must refresh tokens for every installed sub-account (v1 -> v2),
        # not only rows missing from our DB.
        location_records = installed
        _debug_ghl_oauth(
            "company install using all installed locations",
            {
                "installed_count": len(installed),
                "existing_count": len(existing_ids),
                "new_location_ids": new_location_ids,
            },
        )
    if not location_records:
        return None, (
            "No installed locations found. In GHL, select at least one sub-account "
            "when installing the app, then try again."
        )

    try:
        connected, skipped_locations = _connect_locations_from_records(
            company_access_token,
            company_id,
            location_records,
        )
    except Exception as exc:
        return None, str(exc)

    if not connected:
        if skipped_locations:
            return None, (
                f"No active sub-accounts could be connected. "
                f"{len(skipped_locations)} selected location(s) are inactive in GHL."
            )
        return None, (
            "No installed locations found. In GHL, select at least one sub-account "
            "when installing the app, then try again."
        )

    _debug_ghl_oauth(
        "company install complete",
        {
            "connected_count": len(connected),
            "connected_location_ids": [obj.location_id for obj, _ in connected],
            "skipped_count": len(skipped_locations),
        },
    )

    return {"credentials": connected, "skipped_locations": skipped_locations}, None


def exchange_location_oauth_code(
    authorization_code,
    preferred_location_id=None,
    preferred_location_ids=None,
):
    """
    Exchange an OAuth authorization code and connect one or more GHL sub-accounts.

    Supports direct Location installs and agency/Company installs (installedLocations
    + locationToken), matching the latest GHL marketplace OAuth flow.

    Returns (result_dict, None) on success or (None, error_message) on failure.
    result_dict: {"credentials": [(GHLAuthCredentials, created), ...], "skipped_locations": [...]}
    """
    response_data, error = _post_token(
        {
            "grant_type": "authorization_code",
            "client_id": config("GHL_CLIENT_ID"),
            "client_secret": config("GHL_CLIENT_SECRET"),
            "redirect_uri": config("GHL_REDIRECTED_URI"),
            "code": authorization_code,
            "user_type": "Location",
        }
    )
    if error:
        return None, error

    user_type = (response_data.get("userType") or "Location").lower()
    selected_location_ids = list(preferred_location_ids or [])
    if preferred_location_id and preferred_location_id not in selected_location_ids:
        selected_location_ids.insert(0, preferred_location_id)
    if response_data.get("locationId") and response_data.get("locationId") not in selected_location_ids:
        selected_location_ids.append(response_data.get("locationId"))

    _debug_ghl_oauth(
        "exchange_location_oauth_code decision",
        {
            "user_type": user_type,
            "selected_location_ids": selected_location_ids,
            "token_location_id": response_data.get("locationId"),
            "company_id": response_data.get("companyId"),
            "is_bulk_installation": response_data.get("isBulkInstallation"),
        },
    )

    if user_type == "company" or not response_data.get("locationId"):
        return _connect_company_install(
            response_data,
            preferred_location_ids=selected_location_ids or None,
        )

    obj, created = _upsert_location_credentials(response_data)
    return {"credentials": [(obj, created)], "skipped_locations": []}, None


def exchange_agency_oauth_code(authorization_code):
    """
    Exchange an OAuth authorization code for an agency/company token and upsert AgencyToken.

    Returns (agency_token, created) on success, or (None, error_message) on failure.
    """
    response_data, error = _post_token(
        {
            "grant_type": "authorization_code",
            "client_id": config("AGENCY_CLIENT_ID"),
            "client_secret": config("AGENCY_CLIENT_SECRET"),
            "redirect_uri": config("AGENCY_REDIRECT_URI"),
            "code": authorization_code,
            "user_type": "Company",
        }
    )
    if error:
        return None, error

    company_id = response_data.get("companyId")
    if not company_id:
        return None, "OAuth response missing companyId"

    obj, created = AgencyToken.objects.update_or_create(
        company_id=company_id,
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
    return (obj, created), None


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
        response_data, error = _post_token(
            {
                "grant_type": "refresh_token",
                "client_id": config("GHL_CLIENT_ID"),
                "client_secret": config("GHL_CLIENT_SECRET"),
                "refresh_token": refresh_token,
                "user_type": "Location",
                "redirect_uri": config("GHL_REDIRECTED_URI"),
            }
        )
        if error:
            logger.error(
                "GHL location token refresh failed for %s: %s",
                credentials.pk,
                error,
            )
            return False

        if not response_data.get("locationId"):
            logger.error(
                "GHL location token refresh missing locationId for %s: %s",
                credentials.pk,
                response_data,
            )
            return False

        GHLAuthCredentials.objects.update_or_create(
            location_id=response_data.get("locationId"),
            defaults={
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "expires_in": response_data.get("expires_in"),
                "scope": response_data.get("scope"),
                "user_type": response_data.get("userType"),
                "company_id": response_data.get("companyId"),
                "user_id": response_data.get("userId"),
            },
        )
        return True
    except Exception:
        logger.exception("Unexpected error refreshing location token for %s", credentials.pk)
        return False


def _refresh_company_token_with_client(
    refresh_token: str,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict | None:
    if not refresh_token:
        return None

    response_data, error = _post_token(
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "user_type": "Company",
            "redirect_uri": redirect_uri,
        }
    )
    if error or not response_data.get("companyId"):
        return None
    return response_data


def _remint_location_tokens_for_company(company_access_token: str, company_id: str) -> None:
    """Re-mint location tokens for every installed sub-account under this company."""
    try:
        installed = _list_installed_locations(company_access_token, company_id)
        connected, skipped = _connect_locations_from_records(
            company_access_token,
            company_id,
            installed,
        )
        logger.info(
            "Re-minted location tokens for company %s: connected=%s skipped=%s",
            company_id,
            len(connected),
            len(skipped),
        )
    except Exception:
        logger.exception("Failed re-minting location tokens for company %s", company_id)


def refresh_company_token(credentials):
    """Refresh a single CompanyToken row (location app OAuth). Returns True on success."""
    if not credentials.refresh_token:
        logger.warning("Skipping CompanyToken %s: empty refresh_token", credentials.pk)
        return False

    try:
        response_data = _refresh_company_token_with_client(
            credentials.refresh_token,
            client_id=config("GHL_CLIENT_ID"),
            client_secret=config("GHL_CLIENT_SECRET"),
            redirect_uri=config("GHL_REDIRECTED_URI"),
        )
        if not response_data:
            logger.error("Company token refresh failed for %s", credentials.pk)
            return False

        company_id = response_data.get("companyId")
        CompanyToken.objects.update_or_create(
            company_id=company_id,
            defaults={
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "expires_in": response_data.get("expires_in"),
                "scope": response_data.get("scope"),
                "user_type": _clip(response_data.get("userType"), 50),
                "user_id": _clip(response_data.get("userId"), 128),
                "is_bulk_installation": response_data.get("isBulkInstallation", False),
                "token_type": _clip(response_data.get("token_type", "Bearer"), 50),
                "refresh_token_id": _clip(response_data.get("refreshTokenId"), 128),
            },
        )
        _remint_location_tokens_for_company(response_data["access_token"], company_id)
        return True
    except Exception:
        logger.exception("Unexpected error refreshing company token for %s", credentials.pk)
        return False


def refresh_agency_token(credentials):
    """Refresh a single AgencyToken row. Returns True on success."""
    if not credentials.refresh_token:
        logger.warning("Skipping AgencyToken %s: empty refresh_token", credentials.pk)
        return False

    try:
        response_data, error = _post_token(
            {
                "grant_type": "refresh_token",
                "client_id": config("AGENCY_CLIENT_ID"),
                "client_secret": config("AGENCY_CLIENT_SECRET"),
                "refresh_token": credentials.refresh_token,
                "user_type": "Company",
                "redirect_uri": config("AGENCY_REDIRECT_URI"),
            }
        )
        if error:
            logger.error(
                "Agency token refresh failed for %s: %s",
                credentials.pk,
                error,
            )
            return False

        if not response_data.get("companyId"):
            logger.error(
                "Agency token refresh missing companyId for %s: %s",
                credentials.pk,
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
    """Refresh every stored location, company, and agency OAuth token."""
    company_ids_with_token = set(CompanyToken.objects.values_list("company_id", flat=True))
    location_ok = 0
    for creds in GHLAuthCredentials.objects.all():
        if creds.company_id and creds.company_id in company_ids_with_token:
            continue
        location_ok += refresh_location_token(creds)
    company_ok = sum(refresh_company_token(creds) for creds in CompanyToken.objects.all())
    agency_ok = sum(refresh_agency_token(creds) for creds in AgencyToken.objects.all())
    logger.info(
        "Refreshed GHL tokens: %s location(s), %s company, %s agency",
        location_ok,
        company_ok,
        agency_ok,
    )
    return location_ok, company_ok, agency_ok


def _extract_auth_error_detail(response):
    try:
        data = response.json()
        return str(data.get("message") or data.get("error") or response.text)[:500]
    except Exception:
        return (response.text or "")[:500]


def _build_auth_failure_payload(auth_credentials, *, method, url, response, initial_status):
    payload = {
        "alert_type": "ghl_token_auth_failure",
        "reason": (
            "GHL API returned an authentication error after inline token refresh "
            "and one retry. The location likely needs to re-authorize the app."
        ),
        "http_method": method,
        "api_url": url,
        "initial_status_code": initial_status,
        "final_status_code": response.status_code,
        "error_detail": _extract_auth_error_detail(response),
        "occurred_at": timezone.now().isoformat(),
    }
    if auth_credentials is not None:
        payload.update(
            {
                "ghl_account_id": str(auth_credentials.pk),
                "location_id": auth_credentials.location_id,
                "location_name": auth_credentials.location_name,
                "company_id": auth_credentials.company_id,
                "business_email": auth_credentials.business_email,
                "business_phone": auth_credentials.business_phone,
                "contact_name": auth_credentials.contact_name,
                "ghl_contact_email": auth_credentials.ghl_contact_email,
            }
        )
    return payload


def _queue_auth_failure_alert(auth_credentials, *, method, url, response, initial_status):
    webhook_url = getattr(settings, "GHL_AUTH_FAILURE_WEBHOOK_URL", "") or ""
    if not webhook_url:
        return

    dedupe_id = (
        getattr(auth_credentials, "location_id", None)
        or getattr(auth_credentials, "pk", None)
        or "unknown"
    )
    cooldown = getattr(settings, "GHL_AUTH_FAILURE_ALERT_COOLDOWN_SECONDS", 3600)
    dedupe_key = f"ghl_auth_alert:{dedupe_id}"
    if not cache.add(dedupe_key, True, timeout=cooldown):
        logger.info("Skipping duplicate GHL auth failure alert for %s", dedupe_id)
        return

    payload = _build_auth_failure_payload(
        auth_credentials,
        method=method,
        url=url,
        response=response,
        initial_status=initial_status,
    )
    from core.tasks import notify_ghl_auth_failure_task

    notify_ghl_auth_failure_task.delay(payload)
    logger.warning(
        "Queued GHL auth failure alert for location_id=%s (status=%s)",
        payload.get("location_id"),
        response.status_code,
    )


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

    initial_status = response.status_code
    logger.warning(
        "GHL auth error on %s %s (status=%s) — refreshing all tokens and retrying once",
        method,
        url,
        initial_status,
    )
    refresh_all_ghl_tokens()

    if auth_credentials is not None:
        auth_credentials.refresh_from_db()
        headers["Authorization"] = f"Bearer {auth_credentials.access_token}"

    retry_response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if is_ghl_auth_error(retry_response):
        _queue_auth_failure_alert(
            auth_credentials,
            method=method,
            url=url,
            response=retry_response,
            initial_status=initial_status,
        )
    return retry_response
