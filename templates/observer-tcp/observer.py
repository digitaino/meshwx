"""Read-only meshcore observer: connects to a meshcore-proxy TCP endpoint
and republishes received RF packets to an MQTT broker in CoreScope's
expected format. Designed to coexist with an existing meshcore client
(e.g. the Meshcore companion app) connected to the same proxy — we never
send commands to the radio, we only listen.

All config is via env vars (set by docker-compose):

  PROXY_HOST, PROXY_PORT      meshcore-proxy TCP endpoint
  MQTT_HOST, MQTT_PORT        MQTT broker
  MQTT_USERNAME, MQTT_PASSWORD
  MQTT_TOPIC_PREFIX           default "meshcore"
  MQTT_IATA                   3-letter region code, e.g. AUS
  MQTT_TLS                    "true" to use TLS (set when port=443/8883/etc)
  MQTT_TRANSPORT              "tcp" (default) or "websockets"
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from meshcore import MeshCore, EventType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("observer")

_ROUTE_LETTER = {1: "F", 2: "D"}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, "true" if default else "false").lower() in ("1", "true", "yes")


class MqttForwarder:
    def __init__(self, host, port, username, password, topic, transport, tls):
        self.topic = topic
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"observer-{os.environ.get('HOSTNAME', 'unknown')[:12]}",
            transport=transport,
        )
        if username:
            self.client.username_pw_set(username, password)
        if tls:
            self.client.tls_set()
        self.client.max_queued_messages_set(1000)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = lambda *a, **k: log.info("MQTT connected (rc=%s)", a[3])
        self.client.on_disconnect = lambda *a, **k: log.warning("MQTT disconnected (rc=%s)", a[3])
        self.client.connect_async(host, port, keepalive=60)
        self.client.loop_start()
        log.info("MQTT publisher started → %s:%d topic=%s transport=%s tls=%s",
                 host, port, topic, transport, tls)

    def publish(self, rx_log: dict) -> None:
        try:
            now = datetime.now(timezone.utc)
            raw = (rx_log.get("payload") or "").upper()
            packet_len = rx_log.get("payload_length", len(raw) // 2)
            route_code = _ROUTE_LETTER.get(rx_log.get("route_type"),
                                           str(rx_log.get("route_type", "")))
            pkt_type = rx_log.get("payload_type")
            pkt_hash = rx_log.get("pkt_hash", 0)
            envelope = {
                "origin": "meshcore-observer",
                "origin_id": rx_log.get("_origin_id", ""),
                "timestamp": now.isoformat(),
                "type": "PACKET", "direction": "rx",
                "time": now.strftime("%H:%M:%S"),
                "date": now.strftime("%-d/%-m/%Y"),
                "len": str(packet_len),
                "packet_type": str(pkt_type) if pkt_type is not None else "",
                "route": route_code,
                "payload_len": str(packet_len),
                "raw": raw,
                "SNR": str(rx_log.get("snr", "")),
                "RSSI": str(rx_log.get("rssi", "")),
                "score": "1000",
                "hash": f"{pkt_hash:016X}" if isinstance(pkt_hash, int) else str(pkt_hash),
            }
            path = rx_log.get("path")
            if path:
                envelope["path"] = path
            self.client.publish(self.topic, json.dumps(envelope), qos=0)
        except Exception:
            log.exception("MQTT publish failed (non-fatal)")

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


async def main() -> int:
    proxy_host = env("PROXY_HOST", "meshcore-proxy")
    proxy_port = int(env("PROXY_PORT", "5000"))
    mqtt_host = env("MQTT_HOST")
    mqtt_port = int(env("MQTT_PORT", "443"))
    mqtt_user = env("MQTT_USERNAME")
    mqtt_pass = env("MQTT_PASSWORD")
    mqtt_prefix = env("MQTT_TOPIC_PREFIX", "meshcore")
    mqtt_iata = env("MQTT_IATA", "AUS")
    mqtt_tls = env_bool("MQTT_TLS", True)
    mqtt_transport = env("MQTT_TRANSPORT", "websockets")

    if not mqtt_host:
        log.error("MQTT_HOST is required")
        return 1

    log.info("Connecting to meshcore-proxy at %s:%d", proxy_host, proxy_port)
    mc = await MeshCore.create_tcp(proxy_host, proxy_port)
    pubkey = mc.self_info.get("public_key", "") or ""
    if not pubkey:
        log.error("Could not read public_key from radio via proxy — aborting")
        return 1
    log.info("Radio pubkey: %s", pubkey[:16])

    topic = f"{mqtt_prefix}/{mqtt_iata}/{pubkey}/packets"
    fwd = MqttForwarder(mqtt_host, mqtt_port, mqtt_user, mqtt_pass,
                        topic, mqtt_transport, mqtt_tls)

    async def on_rx_log(event):
        payload = dict(event.payload)
        payload["_origin_id"] = pubkey
        fwd.publish(payload)

    mc.subscribe(EventType.RX_LOG_DATA, on_rx_log)
    await mc.start_auto_message_fetching()
    log.info("Subscribed to RX_LOG_DATA — observer is live.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("Shutting down")
    fwd.close()
    await mc.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
