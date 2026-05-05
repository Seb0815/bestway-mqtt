"""Configuration management: environment variables and persistent credentials."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_PATH = Path(os.getenv("CREDENTIALS_PATH", "/app/data/credentials.json"))

# ── MQTT ──────────────────────────────────────────────────────────────────────

MQTT_HOST: str = os.getenv("MQTT_HOST", "")
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER: str | None = os.getenv("MQTT_USER")
MQTT_PASSWORD: str | None = os.getenv("MQTT_PASSWORD")
MQTT_TOPIC_STATE: str = os.getenv("MQTT_TOPIC_STATE", "spa/state")
MQTT_TOPIC_CMD: str = os.getenv("MQTT_TOPIC_CMD", "spa/cmd")
SPA_OFFLINE_TIMEOUT: int = int(os.getenv("SPA_OFFLINE_TIMEOUT", "300"))  # seconds

# ── Bestway API ───────────────────────────────────────────────────────────────

BESTWAY_REGION: str = os.getenv("BESTWAY_REGION", "EU").upper()
BESTWAY_QR_CODE: str | None = os.getenv("BESTWAY_QR_CODE")

API_ENDPOINTS: dict[str, str] = {
    "EU": "https://smarthub-eu.bestwaycorp.com",
    "US": "https://smarthub-us.bestwaycorp.com",
    "CN": "https://smarthub.bestwaycorp.cn",
}

BESTWAY_API_BASE: str = API_ENDPOINTS.get(BESTWAY_REGION, API_ENDPOINTS["EU"])

# ── Bestway App Constants (from decompiled APK — identical for all users) ─────

BESTWAY_APP_ID: str = "AhFLL54HnChhrxcl9ZUJL6QNfolTIB"
BESTWAY_APP_SECRET: str = "4ECvVs13enL5AiYSmscNjvlaisklQDz7vWPCCWXcEFjhWfTmLT"

# ── Credentials ───────────────────────────────────────────────────────────────


@dataclass
class Credentials:
    visitor_id: str
    token: str
    device_id: str
    product_id: str
    service_region: str  # AWS region, e.g. "eu-central-1"


def load_credentials() -> Credentials | None:
    """Load credentials from persistent storage. Returns None if not found."""
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
        return Credentials(**data)
    except Exception as exc:
        _LOGGER.error("Failed to load credentials: %s", exc)
        return None


def save_credentials(creds: Credentials) -> None:
    """Persist credentials to disk with restrictive permissions (0600)."""
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CREDENTIALS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(asdict(creds), indent=2).encode())
    finally:
        os.close(fd)
    _LOGGER.info("Credentials saved to %s", CREDENTIALS_PATH)
