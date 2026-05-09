"""Non-blocking MQTT packet publisher for CoreScope (Cisien-compatible format).

Publishes raw RX_LOG_DATA events from the meshcore radio to topic
`{prefix}/{iata}/{pubkey}/packets` as JSON in the format CoreScope's ingestor
expects (see https://github.com/Cisien/meshcoretomqtt for the canonical schema).
Designed so a broker outage never blocks or crashes the radio event loop.
"""

import json
import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


# Map meshcore Python lib's route_type byte → Cisien single-letter code.
# 1 = FLOOD ("F"), 2 = DIRECT ("D"). Anything else falls back to a string of
# the integer; CoreScope's filter looks for "F"/"D" specifically.
_ROUTE_LETTER = {1: "F", 2: "D"}


class MqttPublisher:
    """Fire-and-forget MQTT publisher running paho's network thread.

    `connect_async` + `loop_start` mean the constructor never blocks even if
    the broker is unreachable. `publish()` queues to an internal buffer; if
    the buffer is full or the client is offline, the message is dropped with
    a debug log. We never raise into the radio dispatcher.
    """

    def __init__(
        self,
        host: str,
        port: int,
        topic_prefix: str,
        iata: str,
        pubkey: str,
        origin: str = "meshcore-weather",
        max_queued: int = 1000,
    ):
        self._topic = f"{topic_prefix}/{iata}/{pubkey}/packets"
        self._host = host
        self._port = port
        self._origin = origin
        self._origin_id = pubkey

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"meshcore-weather-{pubkey[:8]}",
            clean_session=True,
        )
        self._client.max_queued_messages_set(max_queued)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        try:
            self._client.connect_async(host, port, keepalive=60)
            self._client.loop_start()
            logger.info("MQTT publisher started → %s:%d topic=%s", host, port, self._topic)
        except Exception:
            logger.exception("MQTT publisher init failed (non-fatal)")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("MQTT publisher connected to %s:%d", self._host, self._port)
        else:
            logger.warning("MQTT connect returned %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        logger.warning("MQTT disconnected (%s) — paho will reconnect", reason_code)

    def publish_packet(self, rx_log: dict) -> None:
        """Publish one RX_LOG_DATA payload in Cisien/CoreScope format. Never raises.

        CoreScope's DecodePacket expects `raw` to be the meshcore packet hex
        STARTING WITH THE HEADER BYTE (route_type/payload_type/payload_ver
        bitfield). The meshcore Python lib exposes this as `payload` —
        `raw_hex` includes a 2-byte SNR/RSSI prefix that breaks the parser.
        """
        try:
            now = datetime.now(timezone.utc)
            raw = rx_log.get("payload") or ""  # meshcore packet bytes (header + path + ...)
            if not isinstance(raw, str):
                raw = str(raw)
            packet_len = rx_log.get("payload_length", len(raw) // 2)
            route_code = _ROUTE_LETTER.get(rx_log.get("route_type"), str(rx_log.get("route_type", "")))
            pkt_type = rx_log.get("payload_type")
            pkt_hash = rx_log.get("pkt_hash", 0)

            envelope = {
                "origin": self._origin,
                "origin_id": self._origin_id,
                "timestamp": now.isoformat(),
                "type": "PACKET",
                "direction": "rx",
                "time": now.strftime("%H:%M:%S"),
                "date": now.strftime("%-d/%-m/%Y"),
                "len": str(packet_len),
                "packet_type": str(pkt_type) if pkt_type is not None else "",
                "route": route_code,
                "payload_len": str(packet_len),
                "raw": raw.upper(),
                "SNR": str(rx_log.get("snr", "")),
                "RSSI": str(rx_log.get("rssi", "")),
                "score": "1000",
                "hash": f"{pkt_hash:016X}" if isinstance(pkt_hash, int) else str(pkt_hash),
            }
            path = rx_log.get("path")
            if path:
                envelope["path"] = path

            body = json.dumps(envelope)
            info = self._client.publish(self._topic, body, qos=0)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.debug("MQTT publish queued/dropped rc=%s", info.rc)
        except Exception:
            logger.exception("MQTT publish failed (non-fatal)")

    def close(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
            logger.info("MQTT publisher closed")
        except Exception:
            logger.debug("MQTT close error (ignored)", exc_info=True)
