# Copilot Instructions – bestway-mqtt

## Projekt-Überblick

Dieses Projekt ist ein asynchroner Python-Bridge, der den Bestway/Lay-Z-Spa Whirlpool-Status über WebSocket aus der Bestway SmartHub Cloud liest und über MQTT veröffentlicht. Steuerbefehle kommen umgekehrt über MQTT an und werden per HTTPS/WebSocket an die Cloud weitergeleitet.

## Technologie-Stack

- **Python 3.12**, vollständig async (`asyncio`)
- `aiohttp` – HTTP-Client für die Bestway REST-API
- `websockets` – AWS IoT WebSocket-Verbindung
- `paho-mqtt` – MQTT-Broker-Kommunikation (paho läuft in eigenem Thread)
- `pycryptodome` – AES-Verschlüsselung der Steuerbefehle
- Docker / Docker Compose für Deployment

## Codestruktur

```
app/
  main.py              # Einstiegspunkt: --onboard oder normaler Bridge-Betrieb
  config.py            # Env-Variablen + Credential-Persistenz (data/credentials.json)
  bestway/
    auth.py            # Login, Visitor-ID-Generierung, QR-Code-Binding
    api.py             # Geräteerkennung, Shadow-Parsing → SpaState, Befehlsversand
    encryption.py      # AES-Verschlüsselung der Device-Commands
    websocket.py       # AWS IoT WebSocket-Client, automatischer Reconnect + Token-Refresh
  mqtt/
    client.py          # paho-mqtt Wrapper: publish_state(), subscribe cmd-topics
```

## Wichtige Konventionen

- **Async überall**: Alle I/O-Operationen (HTTP, WebSocket) sind async. Der MQTT-Client (paho) läuft in einem eigenen Thread; Übergaben an den asyncio-Loop laufen über `loop.call_soon_threadsafe()`.
- **Credentials** werden in `data/credentials.json` persistiert und beim Start geladen/refreshed. Nie hartcodieren.
- **API-Herkunft**: Protokolldetails (Endpunkte, Auth-Flow, Befehlsformat, Verschlüsselung) stammen aus [cdpuk/ha-bestway](https://github.com/cdpuk/ha-bestway). Änderungen an `bestway/` sollten mit dem dortigen Quellcode abgeglichen werden.
- **SpaState** (`bestway/api.py`) ist die zentrale Datenklasse. `to_dict()` liefert das MQTT-JSON-Payload.
- **Keine Breaking Changes** an MQTT-Topics ohne Anpassung der Konsumenten (z. B. IP-Symcon).

## Typische Erweiterungspunkte

- Neue Befehle → `api.py` (Befehlsversand) + `mqtt/client.py` (neues Sub-Topic)
- Neues State-Feld → `SpaState`-Dataclass + `parse_shadow_update()` + `to_dict()`
- Neue API-Region → `API_ENDPOINTS`-Dict in `config.py`

## Sicherheit

- Credentials (Token, Visitor-ID, Device-ID) niemals in Logs ausgeben (nur die ersten 12 Zeichen für Debug-Zwecke).
- `.env` und `data/credentials.json` sind in `.gitignore`; nie committen.
