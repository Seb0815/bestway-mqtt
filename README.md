# bestway-mqtt

A Python-based MQTT bridge for Bestway / Lay-Z-Spa hot tubs.  
The bridge connects to the Bestway SmartHub Cloud API via a WebSocket connection, publishes the device state to an MQTT broker, and forwards control commands (heating, filter, temperature, etc.) received via MQTT back to the cloud.

## Credits

The API communication (authentication, device discovery, AWS IoT WebSocket, command encryption) is based on the reverse-engineering work of the Home Assistant integration project:

> **[cdpuk/ha-bestway](https://github.com/cdpuk/ha-bestway)** – Home Assistant integration for Bestway/Lay-Z-Spa

This project would not have been possible without the protocol details, API endpoints, and command encryption knowledge documented there.

---

## Architecture

```
Bestway Cloud API  ←──WebSocket──→  bridge  ──MQTT──→  Broker (e.g. IP-Symcon)
                                      │
                                      └──MQTT cmd──←  Broker
```

| Module | Description |
|---|---|
| `bestway/auth.py` | Authentication, visitor ID generation, QR code binding |
| `bestway/api.py` | Device discovery, shadow parsing, command dispatch |
| `bestway/websocket.py` | AWS IoT WebSocket, token refresh, auto-reconnect |
| `bestway/encryption.py` | AES encryption of device commands |
| `mqtt/client.py` | paho-mqtt wrapper, publish & subscribe |
| `config.py` | Environment variables, credential persistence |
| `main.py` | Entry point: onboarding mode & normal operation |

---

## Requirements

- Docker & Docker Compose **or** Python 3.12+
- A running MQTT broker (e.g. Mosquitto)
- A Bestway hot tub with a SmartHub module
- The QR code of the SmartHub module (for the one-time onboarding)

---

## Setup

### 1. Configure environment variables

Copy `.env.example` to `.env` and fill in the values:

```env
# MQTT broker
MQTT_HOST=192.168.1.100
MQTT_PORT=1883
MQTT_USER=
MQTT_PASSWORD=
MQTT_TOPIC_STATE=spa/state
MQTT_TOPIC_CMD=spa/cmd

# Bestway API
BESTWAY_REGION=EU          # EU | US | CN
BESTWAY_QR_CODE=           # Only required for --onboard
```

### 2. Onboarding (one-time)

On first run, the SmartHub QR code must be scanned and a visitor account created:

```bash
docker compose run --rm bridge python main.py --onboard
```

Credentials are stored in `data/credentials.json` and loaded automatically on subsequent starts.

### 3. Start the bridge

```bash
docker compose up -d
```

---

## MQTT Topics

### State (published by the bridge)

**Topic:** `spa/state`  
**Payload (JSON):**

```json
{
  "power": true,
  "heater": 1,
  "filter": false,
  "bubbles": 0,
  "temp_now": 36.5,
  "temp_set": 38,
  "temp_unit": "C",
  "locked": false,
  "is_online": true,
  "error": 0
}
```

### Commands (subscribed by the bridge)

**Base topic:** `spa/cmd`  
Sub-topics and payloads:

| Topic | Payload | Description |
|---|---|---|
| `spa/cmd/power` | `{"state": true}` | Power on/off |
| `spa/cmd/heater` | `{"state": true}` | Heater on/off |
| `spa/cmd/filter` | `{"state": true}` | Filter on/off |
| `spa/cmd/temperature` | `{"value": 38}` | Set target temperature (10–40 °C) |
| `spa/cmd/bubbles` | `{"level": 40}` | Massage jets (0 / 40 / 100) |

---

## Local Development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export MQTT_HOST=localhost
cd app
python main.py
```

---

## License

See [LICENSE](LICENSE).
