"""AWS IoT WebSocket client with automatic reconnect and heartbeat.

Connects to the regional AWS API Gateway endpoint for the device and receives
real-time shadow delta updates. One instance per device.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Callable, Awaitable
from typing import Any

import websockets

_LOGGER = logging.getLogger(__name__)

# Regional AWS API Gateway endpoints (from official app ServiceConfig.java)
_ENDPOINTS: dict[str, str] = {
    "eu-central-1": "wss://7lv67j5lbh.execute-api.eu-central-1.amazonaws.com/prod",
    "us-west-1":    "wss://9i661wi8f9.execute-api.us-west-1.amazonaws.com/prod",
    "cn-north-1":   "wss://fu9gsv4dxh.execute-api.cn-north-1.amazonaws.com.cn/prod",
}

# Exponential backoff delays in seconds (extends to 30 min for long outages)
_BACKOFF = [3, 6, 12, 24, 48, 60, 120, 300, 600, 1800]

StateCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
TokenRefreshCallback = Callable[[], Awaitable[str]]


class SpaWebSocket:
    """Per-device WebSocket connection to AWS IoT.

    Args:
        device_id:       Target device ID.
        service_region:  AWS region string, e.g. "eu-central-1".
        token:           Current auth token (JWT).
        on_state:        Async or sync callback(shadow_dict) called on updates.
        on_token_refresh: Async callable that returns a fresh token (optional).
    """

    def __init__(
        self,
        device_id: str,
        service_region: str,
        token: str,
        on_state: StateCallback,
        on_token_refresh: TokenRefreshCallback | None = None,
    ) -> None:
        self._device_id = device_id
        self._service_region = service_region
        self._token = token
        self._on_state = on_state
        self._on_token_refresh = on_token_refresh

        self._ws: Any = None  # websockets.ClientConnection
        self._running = False
        self._reconnect_count = 0
        self._seq = 0

    # ── Public interface ──────────────────────────────────────────────────────

    def update_token(self, token: str) -> None:
        self._token = token

    async def start(self) -> None:
        """Connect and start background tasks. Returns immediately."""
        self._running = True
        await self._connect()

    async def stop(self) -> None:
        """Gracefully disconnect and stop all tasks."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    # ── Connection management ─────────────────────────────────────────────────

    @property
    def _url(self) -> str:
        return _ENDPOINTS.get(self._service_region, _ENDPOINTS["eu-central-1"])

    async def _connect(self) -> None:
        ssl_ctx = ssl.create_default_context()
        short_id = self._device_id[:12]
        try:
            self._ws = await websockets.connect(
                self._url,
                additional_headers={"Authorization": self._token},
                ssl=ssl_ctx,
                ping_interval=None,  # we handle heartbeat manually
            )
            self._reconnect_count = 0
            _LOGGER.info("WebSocket connected for device %s", short_id)

            listen_task = asyncio.create_task(self._listen())
            heartbeat_task = asyncio.create_task(self._heartbeat())

            # Wait until either task ends, then cancel the other
            done, pending = await asyncio.wait(
                [listen_task, heartbeat_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except Exception as exc:
            err = str(exc)
            _LOGGER.error("WebSocket error for device %s: %s", short_id, err)

            # Token expired → refresh and retry immediately
            if "HTTP 400" in err and self._on_token_refresh is not None:
                try:
                    new_token = await self._on_token_refresh()
                    self._token = new_token
                    _LOGGER.info("Token refreshed, reconnecting immediately")
                    await self._connect()
                    return
                except Exception as refresh_exc:
                    _LOGGER.error("Token refresh failed: %s", refresh_exc)

        # Always attempt reconnect after disconnection (unless stopped)
        await self._schedule_reconnect()

    async def _listen(self) -> None:
        short_id = self._device_id[:12]
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    _LOGGER.warning("Malformed JSON from WebSocket")
        except websockets.exceptions.ConnectionClosed:
            _LOGGER.warning("WebSocket closed for device %s", short_id)
        except Exception as exc:
            _LOGGER.error("Listen loop error for device %s: %s", short_id, exc)
        else:
            _LOGGER.warning("WebSocket disconnected cleanly for device %s", short_id)

    async def _heartbeat(self) -> None:
        while self._running and self._ws:
            try:
                await asyncio.sleep(30)
                if not self._running or not self._ws:
                    break
                msg = {
                    "action": "heartbeat",
                    "req_event": "heartbeat_req",
                    "seq_id": self._seq,
                    "req_count": 1,
                    "req": None,
                }
                await self._ws.send(json.dumps(msg))
                await self._ws.ping()
                self._seq += 1
                _LOGGER.debug("Heartbeat sent for device %s", self._device_id[:12])
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _LOGGER.warning("Heartbeat failed: %s", exc)
                break

    async def _handle_message(self, data: dict[str, Any]) -> None:
        """Forward shadow state updates to the callback."""
        if "state" not in data:
            return
        _LOGGER.debug("Shadow update for device %s", self._device_id[:12])
        result = self._on_state(data)
        if asyncio.iscoroutine(result):
            await result

    async def _schedule_reconnect(self) -> None:
        if not self._running:
            return
        idx = min(self._reconnect_count, len(_BACKOFF) - 1)
        delay = _BACKOFF[idx]
        self._reconnect_count += 1
        _LOGGER.info(
            "Reconnecting device %s in %ds (attempt %d)",
            self._device_id[:12],
            delay,
            self._reconnect_count,
        )
        await asyncio.sleep(delay)
        if self._running:
            await self._connect()
