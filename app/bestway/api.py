"""Bestway AWS IoT device API: discovery, shadow fetch, commands."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from time import time
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientSession

import config as _cfg
from .encryption import encrypt_command

_LOGGER = logging.getLogger(__name__)

_APP_ID = _cfg.BESTWAY_APP_ID
_APP_SECRET = _cfg.BESTWAY_APP_SECRET
_TIMEOUT = 10


@dataclass
class DeviceInfo:
    device_id: str
    product_id: str
    service_region: str  # e.g. "eu-central-1"
    alias: str


@dataclass
class SpaState:
    """Normalised spa state, suitable for MQTT publishing."""
    power: bool
    heater: int       # 0=off 1=on 3=heating 4=target_reached
    filter: bool
    bubbles: int      # 0/40/100
    temp_now: int
    temp_set: int
    temp_unit: int    # 1=celsius 2=fahrenheit
    locked: bool
    is_online: bool
    error: int
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "power": self.power,
            "heater": self.heater,
            "filter": self.filter,
            "bubbles": self.bubbles,
            "temp_now": self.temp_now,
            "temp_set": self.temp_set,
            "temp_unit": "C" if self.temp_unit == 1 else "F",
            "locked": self.locked,
            "is_online": self.is_online,
            "error": self.error,
        }


def _nonce() -> str:
    return secrets.token_hex(16)


def _make_headers(token: str, api_base: str) -> dict[str, str]:
    nonce = _nonce()
    ts = str(int(time()))
    sign = hashlib.md5(f"{_APP_ID}{_APP_SECRET}{nonce}{ts}".encode()).hexdigest().upper()
    return {
        "pushtype": "fcm",
        "appid": _APP_ID,
        "nonce": nonce,
        "ts": ts,
        "accept-language": "en",
        "sign": sign,
        "Authorization": f"token {token}",
        "Host": urlparse(api_base).hostname,
        "Connection": "Keep-Alive",
        "User-Agent": "okhttp/4.9.0",
        "Content-Type": "application/json; charset=UTF-8",
    }


class BestwayApi:
    def __init__(self, session: ClientSession, token: str, api_base: str) -> None:
        self._session = session
        self._token = token
        self._api_base = api_base

    def update_token(self, token: str) -> None:
        self._token = token

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    async def _get(self, path: str) -> dict[str, Any]:
        headers = _make_headers(self._token, self._api_base)
        async with self._session.get(
            f"{self._api_base}{path}", headers=headers, timeout=_TIMEOUT
        ) as resp:
            if resp.status in (400, 401):
                raise PermissionError("Token expired or invalid")
            resp.raise_for_status()
            return dict(await resp.json())

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = _make_headers(self._token, self._api_base)
        async with self._session.post(
            f"{self._api_base}{path}",
            headers=headers,
            json=payload,
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status in (400, 401):
                raise PermissionError("Token expired or invalid")
            resp.raise_for_status()
            return dict(await resp.json())

    # ── Device discovery ──────────────────────────────────────────────────────

    async def discover_device(self) -> DeviceInfo:
        """Walk homes → rooms → devices and return the first spa found."""
        homes_resp = await self._get("/api/enduser/homes")
        homes = homes_resp.get("data", {}).get("list", [])
        if not homes:
            raise RuntimeError("No homes found in account")

        for home in homes:
            rooms_resp = await self._get(f"/api/enduser/home/rooms?home_id={home['id']}")
            for room in rooms_resp.get("data", {}).get("list", []):
                devices_resp = await self._get(
                    f"/api/enduser/home/room/devices?room_id={room['id']}"
                )
                for dev in devices_resp.get("data", {}).get("list", []):
                    device_id = dev["device_id"]
                    product_id = dev.get("product_id", "").strip()
                    service_region = dev.get("service_region", "eu-central-1")
                    alias = dev.get("device_alias") or dev.get("device_name") or device_id[:12]
                    _LOGGER.info("Discovered device: %s (%s)", alias, device_id[:12])
                    return DeviceInfo(
                        device_id=device_id,
                        product_id=product_id,
                        service_region=service_region,
                        alias=alias,
                    )

        raise RuntimeError("No devices found in any home/room")

    # ── State fetch ───────────────────────────────────────────────────────────

    async def fetch_state(self, device_id: str, product_id: str) -> SpaState:
        """Fetch current device shadow and return a normalised SpaState."""
        resp = await self._post(
            "/api/device/thing_shadow/",
            {"device_id": device_id, "product_id": product_id},
        )
        raw = resp.get("data", {})
        state: dict[str, Any] = (
            raw.get("state", {}).get("reported")
            or raw.get("state", {}).get("desired")
            or raw.get("state", raw)
        )
        return _parse_state(state)

    # ── Commands ──────────────────────────────────────────────────────────────

    async def set_power(self, device_id: str, product_id: str, state: bool) -> None:
        await self._send_command(device_id, product_id, {"power_state": 1 if state else 0})

    async def set_heater(self, device_id: str, product_id: str, state: bool) -> None:
        await self._send_command(device_id, product_id, {"heater_state": 1 if state else 0})

    async def set_filter(self, device_id: str, product_id: str, state: bool) -> None:
        await self._send_command(device_id, product_id, {"filter_state": 1 if state else 0})

    async def set_temperature(self, device_id: str, product_id: str, value: int) -> None:
        await self._send_command(device_id, product_id, {"temperature_setting": value})

    async def set_bubbles(self, device_id: str, product_id: str, level: int) -> None:
        """level: 0=off, 40=medium, 100=high"""
        await self._send_command(device_id, product_id, {"wave_state": level})

    async def _send_command(
        self, device_id: str, product_id: str, updates: dict[str, Any]
    ) -> None:
        headers = _make_headers(self._token, self._api_base)
        sign = headers["sign"]

        shadow_payload = {"state": {"desired": updates}}
        desired_json = json.dumps(shadow_payload, separators=(",", ":"))

        command = json.dumps(
            {"device_id": device_id, "product_id": product_id, "desired": desired_json},
            separators=(",", ":"),
        )

        encrypted = encrypt_command(sign, _APP_SECRET, command)

        # Try v2 (encrypted) first, fall back to v1 (plain)
        try:
            result = await self._post_raw(
                "/api/v2/device/command",
                {"encrypted_data": encrypted},
                headers=headers,
            )
            if result.get("code") == 0:
                _LOGGER.debug("v2 command sent: %s", updates)
                return
            _LOGGER.warning("v2 command returned code %s, falling back", result.get("code"))
        except Exception as exc:
            _LOGGER.warning("v2 command failed (%s), falling back to v1", exc)

        # v1 fallback
        result = await self._post(
            "/api/device/command/",
            {"device_id": device_id, "product_id": product_id, "desired": {"state": {"desired": updates}}},
        )
        if result.get("code") != 0:
            _LOGGER.error("v1 command also failed: %s", result)

    async def _post_raw(
        self, path: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        async with self._session.post(
            f"{self._api_base}{path}",
            headers=headers,
            json=payload,
            timeout=_TIMEOUT,
        ) as resp:
            return dict(await resp.json())


# ── State parsing ─────────────────────────────────────────────────────────────

def _parse_state(s: dict[str, Any]) -> SpaState:
    """Normalise raw AWS IoT shadow fields into a SpaState."""
    return SpaState(
        power=bool(s.get("power_state") == 1),
        heater=int(s.get("heater_state", 0)),
        filter=bool(s.get("filter_state", 0)),
        bubbles=int(s.get("wave_state", 0)),
        temp_now=int(s.get("water_temperature", 0)),
        temp_set=int(s.get("temperature_setting", 0)),
        temp_unit=int(s.get("temperature_unit", 1)),
        locked=bool(s.get("locked", 0)),
        is_online=bool(s.get("is_online", True)),
        error=int(s.get("error_code") or 0),
        raw=s,
    )


def parse_shadow_update(shadow: dict[str, Any]) -> SpaState | None:
    """Parse an incoming WebSocket shadow delta message into a SpaState.

    Returns None if the message does not contain state data.
    """
    state = (
        shadow.get("state", {}).get("reported")
        or shadow.get("state", {}).get("desired")
    )
    if not state:
        return None
    return _parse_state(state)
