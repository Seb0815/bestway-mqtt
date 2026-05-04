"""MQTT client wrapper around paho-mqtt.

Publishes spa state to IP-Symcon and subscribes to command topics.
Handles automatic reconnection transparently.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

CommandCallback = Callable[[str, dict[str, Any]], None]
"""Called with (sub_topic, payload_dict) when a command message arrives.

sub_topic is the part after the base cmd topic, e.g. "heater", "power",
"temperature", "filter".
"""


class MqttClient:
    """Thread-safe paho-mqtt wrapper.

    Args:
        host:            Broker hostname or IP.
        port:            Broker TCP port (default 1883).
        user:            Optional username.
        password:        Optional password.
        topic_state:     Topic where spa state is published (e.g. "spa/state").
        topic_cmd:       Base topic for incoming commands (e.g. "spa/cmd").
        on_command:      Callback invoked for every command message received.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        topic_state: str,
        topic_cmd: str,
        on_command: CommandCallback,
    ) -> None:
        self._topic_state = topic_state
        self._topic_cmd = topic_cmd
        self._on_command = on_command

        self._client = mqtt.Client(
            client_id="bestway-bridge",
        )

        if user:
            self._client.username_pw_set(user, password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        self._client.connect_async(host, port, keepalive=60)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the paho background thread."""
        self._client.loop_start()

    def stop(self) -> None:
        """Stop the paho background thread and disconnect."""
        self._client.loop_stop()
        self._client.disconnect()

    def publish_state(self, state_dict: dict[str, Any]) -> None:
        """Publish spa state JSON to the state topic (QoS 1, retain=True)."""
        payload = json.dumps(state_dict)
        result = self._client.publish(self._topic_state, payload, qos=1, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.warning("MQTT publish failed: rc=%s", result.rc)
        else:
            _LOGGER.debug("Published state: %s", payload)

    def publish_offline(self) -> None:
        """Publish an offline marker so subscribers know the bridge lost connection."""
        self._client.publish(
            self._topic_state,
            json.dumps({"online": False}),
            qos=1,
            retain=True,
        )

    # ── paho callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, rc: Any, props: Any = None) -> None:
        if rc == 0:
            _LOGGER.info("MQTT connected")
            # Subscribe to base cmd topic and all sub-topics
            client.subscribe([(self._topic_cmd, 1), (f"{self._topic_cmd}/#", 1)])
            _LOGGER.info("Subscribed to %s and %s/#", self._topic_cmd, self._topic_cmd)
        else:
            _LOGGER.error("MQTT connect failed: rc=%s", rc)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            _LOGGER.warning("Ignoring malformed command payload on %s", msg.topic)
            return

        if msg.topic == self._topic_cmd:
            # Combined format on base topic: {"topic": "power", "payload": {"state": true}}
            sub = payload.get("topic")
            if not sub or not isinstance(sub, str):
                _LOGGER.warning("Missing or invalid 'topic' field in combined command")
                return
            inner = payload.get("payload", {})
        else:
            # Sub-topic format: spa/cmd/power  with payload {"state": true}
            prefix = f"{self._topic_cmd}/"
            if not msg.topic.startswith(prefix):
                return
            sub = msg.topic[len(prefix):]
            inner = payload

        _LOGGER.info("Command received: %s = %s", sub, inner)
        try:
            self._on_command(sub, inner)
        except Exception as exc:
            _LOGGER.error("Command handler error: %s", exc)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: Any, props: Any = None) -> None:
        if rc != 0:
            _LOGGER.warning("MQTT unexpectedly disconnected (rc=%s), paho will reconnect", rc)
        else:
            _LOGGER.info("MQTT disconnected")
