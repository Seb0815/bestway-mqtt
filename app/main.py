"""Bestway MQTT Bridge — entry point.

Usage:
    python main.py             # normal operation (requires credentials.json)
    python main.py --onboard   # first-time setup via QR code

Environment variables (see .env.example):
    BESTWAY_QR_CODE   required for --onboard
    MQTT_HOST         required always
    ... (see config.py)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

import aiohttp

import config
from bestway.api import BestwayApi, parse_shadow_update
from bestway.auth import authenticate, bind_qr_code, generate_visitor_id
from bestway.websocket import SpaWebSocket
from mqtt.client import MqttClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("bridge")


# ── Onboarding ────────────────────────────────────────────────────────────────

async def onboard() -> None:
    """First-time setup: authenticate, bind QR code, discover device, save credentials."""
    qr = config.BESTWAY_QR_CODE
    if not qr:
        _LOGGER.error("BESTWAY_QR_CODE is not set in environment / .env")
        sys.exit(1)

    _LOGGER.info("Starting onboarding…")

    async with aiohttp.ClientSession() as session:
        visitor_id = generate_visitor_id()
        _LOGGER.info("Generated visitor_id: %s", visitor_id)

        token = await authenticate(session, visitor_id, config.BESTWAY_API_BASE)
        _LOGGER.info("Token obtained")

        await bind_qr_code(session, qr, visitor_id, token, config.BESTWAY_API_BASE)
        _LOGGER.info("QR code bound successfully")

        api = BestwayApi(session, token, config.BESTWAY_API_BASE)
        device = await api.discover_device()
        _LOGGER.info(
            "Device: %s | id=%s | product=%s | region=%s",
            device.alias, device.device_id[:12], device.product_id, device.service_region,
        )

    creds = config.Credentials(
        visitor_id=visitor_id,
        token=token,
        device_id=device.device_id,
        product_id=device.product_id,
        service_region=device.service_region,
    )
    config.save_credentials(creds)
    _LOGGER.info("Onboarding complete — run without --onboard to start the bridge.")


# ── Normal bridge operation ───────────────────────────────────────────────────

async def run() -> None:
    creds = config.load_credentials()
    if creds is None:
        _LOGGER.error(
            "No credentials.json found. Run with --onboard first."
        )
        sys.exit(1)

    stop_event = asyncio.Event()

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    async with aiohttp.ClientSession() as session:
        # Refresh token on every start
        try:
            token = await authenticate(session, creds.visitor_id, config.BESTWAY_API_BASE)
            creds.token = token
            config.save_credentials(creds)
        except Exception as exc:
            _LOGGER.warning("Token refresh failed (%s), using stored token", exc)

        api = BestwayApi(session, creds.token, config.BESTWAY_API_BASE)

        # ── MQTT command handler (runs on paho thread) ─────────────────────

        pending_commands: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def on_command(sub: str, payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(pending_commands.put_nowait, (sub, payload))

        mqtt_client = MqttClient(
            host=config.MQTT_HOST,
            port=config.MQTT_PORT,
            user=config.MQTT_USER,
            password=config.MQTT_PASSWORD,
            topic_state=config.MQTT_TOPIC_STATE,
            topic_cmd=config.MQTT_TOPIC_CMD,
            on_command=on_command,
        )
        mqtt_client.start()

        # ── WebSocket state callback ───────────────────────────────────────

        def on_spa_state(shadow: dict[str, Any]) -> None:
            state = parse_shadow_update(shadow)
            if state:
                mqtt_client.publish_state(state.to_dict())

        async def token_refresh() -> str:
            new_token = await authenticate(session, creds.visitor_id, config.BESTWAY_API_BASE)
            creds.token = new_token
            api.update_token(new_token)
            config.save_credentials(creds)
            _LOGGER.info("Token refreshed")
            return new_token

        ws = SpaWebSocket(
            device_id=creds.device_id,
            service_region=creds.service_region,
            token=creds.token,
            on_state=on_spa_state,
            on_token_refresh=token_refresh,
        )

        # ── Command dispatch loop ──────────────────────────────────────────

        async def dispatch_commands() -> None:
            while not stop_event.is_set():
                try:
                    sub, payload = await asyncio.wait_for(pending_commands.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                device_id = creds.device_id
                product_id = creds.product_id

                try:
                    if sub == "power":
                        await api.set_power(device_id, product_id, bool(payload.get("state")))
                    elif sub == "heater":
                        await api.set_heater(device_id, product_id, bool(payload.get("state")))
                    elif sub == "filter":
                        await api.set_filter(device_id, product_id, bool(payload.get("state")))
                    elif sub == "temperature":
                        value = int(payload.get("value", 38))
                        await api.set_temperature(device_id, product_id, value)
                    elif sub == "bubbles":
                        level = int(payload.get("level", 0))
                        await api.set_bubbles(device_id, product_id, level)
                    else:
                        _LOGGER.warning("Unknown command topic: %s", sub)
                except Exception as exc:
                    _LOGGER.error("Command execution failed (%s): %s", sub, exc)

        # ── Start & wait ───────────────────────────────────────────────────

        _LOGGER.info(
            "Bridge running — device=%s, MQTT=%s:%s",
            creds.device_id[:12], config.MQTT_HOST, config.MQTT_PORT,
        )

        try:
            await asyncio.gather(
                ws.start(),
                dispatch_commands(),
                stop_event.wait(),
            )
        finally:
            _LOGGER.info("Shutting down…")
            mqtt_client.publish_offline()
            await ws.stop()
            mqtt_client.stop()
            _LOGGER.info("Bridge stopped.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--onboard" in sys.argv:
        asyncio.run(onboard())
    else:
        asyncio.run(run())
