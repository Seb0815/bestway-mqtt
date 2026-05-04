"""Bestway cloud authentication: visitor registration, token fetch, QR-bind.

All endpoint paths and header field names are taken from the official app
(ServiceConfig.java / LoginApi.java) via reverse engineering.
"""

from __future__ import annotations

import hashlib
import logging
import random
import secrets
import string
from time import time

from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

# Application credentials from decompiled APK — identical for all users.
_APP_ID = "AhFLL54HnChhrxcl9ZUJL6QNfolTIB"
_APP_SECRET = "4ECvVs13enL5AiYSmscNjvlaisklQDz7vWPCCWXcEFjhWfTmLT"

_TIMEOUT = 10


class AuthError(Exception):
    """Raised when authentication or QR-bind fails."""


def generate_visitor_id() -> str:
    """Generate a new random 16-character hex visitor ID."""
    return secrets.token_hex(8)


def _nonce() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=32))


def _sign(nonce: str, timestamp: str) -> str:
    raw = f"{_APP_ID}{_APP_SECRET}{nonce}{timestamp}"
    return hashlib.md5(raw.encode()).hexdigest().upper()


def _base_headers(nonce: str, timestamp: str, sign: str, token: str | None = None) -> dict[str, str]:
    auth = f"token {token}" if token else "token"
    return {
        "pushtype": "fcm",
        "appid": _APP_ID,
        "nonce": nonce,
        "ts": timestamp,
        "accept-language": "en",
        "sign": sign,
        "Authorization": auth,
        "Host": "smarthub-eu.bestwaycorp.com",
        "Connection": "Keep-Alive",
        "User-Agent": "okhttp/4.9.0",
        "Content-Type": "application/json; charset=UTF-8",
    }


async def authenticate(session: ClientSession, visitor_id: str, api_base: str) -> str:
    """Register visitor and return a fresh token.

    Raises:
        AuthError: if the API does not return a token.
    """
    nonce = _nonce()
    ts = str(int(time()))
    sign = _sign(nonce, ts)

    client_id = secrets.token_urlsafe(11)[:15].replace("-", "").replace("_", "").lower()

    payload = {
        "app_id": _APP_ID,
        "brand": "",
        "client_id": client_id,
        "lan_code": "en",
        "location": "GB",
        "marketing_notification": 0,
        "push_type": "fcm",
        "registration_id": "",
        "timezone": "GMT",
        "visitor_id": visitor_id,
    }

    headers = _base_headers(nonce, ts, sign)

    async with session.post(
        f"{api_base}/api/enduser/visitor",
        headers=headers,
        json=payload,
        ssl=False,
        timeout=_TIMEOUT,
    ) as resp:
        data = await resp.json()

    token = data.get("data", {}).get("token")
    if not token:
        raise AuthError(f"No token in response: {data}")

    _LOGGER.info("Authenticated visitor %s", visitor_id[:12])
    return str(token)


async def bind_qr_code(
    session: ClientSession,
    qr_code: str,
    visitor_id: str,
    token: str,
    api_base: str,
) -> dict:
    """Bind the spa device to this visitor account via QR code.

    Args:
        qr_code: The string from the app's sharing QR code, must start with
                 "RW_Share_".

    Returns:
        Device info dict from the API.

    Raises:
        AuthError: if the QR code is invalid, expired, or already used.
    """
    if not qr_code.startswith("RW_Share_"):
        raise AuthError(f"Invalid QR code format (expected RW_Share_...): {qr_code!r}")

    nonce = _nonce()
    ts = str(int(time()))
    sign = _sign(nonce, ts)

    payload = {
        "vercode": qr_code,
        "visitor_id": visitor_id,
        "push_type": "android",
    }

    headers = _base_headers(nonce, ts, sign, token=token)
    headers["pushtype"] = "android"

    async with session.post(
        f"{api_base}/api/enduser/grant_device",
        headers=headers,
        json=payload,
        ssl=False,
        timeout=_TIMEOUT,
    ) as resp:
        if resp.status in (400, 401):
            raise AuthError("QR code invalid, expired, or already used")
        resp.raise_for_status()
        data = await resp.json()

    result = data.get("data")
    if not result:
        raise AuthError(f"No device data in bind response: {data}")

    _LOGGER.info("QR bind successful: %s", result)
    return dict(result)
