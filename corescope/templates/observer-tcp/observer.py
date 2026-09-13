"""Read-only meshcore observer: republishes received RF packets to an MQTT
broker in CoreScope's expected format. Two connection modes:

  OBSERVER_MODE=tcp    (default) connects to a meshcore-proxy TCP endpoint;
                       coexists with an existing client (e.g. Companion app)
                       on the same proxy.
  OBSERVER_MODE=serial direct USB connection to the radio. Use when the
                       observer is the only thing touching the radio
                       (no proxy, no Companion app sharing it).

We never send commands to the radio in either mode — we only listen.

All config is via env vars:

  OBSERVER_MODE               "tcp" (default) or "serial"
  PROXY_HOST, PROXY_PORT      meshcore-proxy TCP endpoint (tcp mode)
  SERIAL_PORT                 e.g. /dev/serial/by-id/usb-... (serial mode)
  SERIAL_BAUD                 default 115200 (serial mode)
  MQTT_HOST, MQTT_PORT        MQTT broker
  MQTT_USERNAME, MQTT_PASSWORD
  MQTT_TOPIC_PREFIX           default "meshcore"
  MQTT_IATA                   3-letter region code, e.g. AUS
  MQTT_TLS                    "true" to use TLS (set when port=443/8883/etc)
  MQTT_TRANSPORT              "tcp" (default) or "websockets"
  MESHCORE_TIMEOUT            seconds to wait for APPSTART handshake (default 30)
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
    mode = env("OBSERVER_MODE", "tcp").lower()
    proxy_host = env("PROXY_HOST", "meshcore-proxy")
    proxy_port = int(env("PROXY_PORT", "5000"))
    serial_port = env("SERIAL_PORT")
    serial_baud = int(env("SERIAL_BAUD", "115200"))
    mqtt_host = env("MQTT_HOST")
    mqtt_port = int(env("MQTT_PORT", "443"))
    mqtt_user = env("MQTT_USERNAME")
    mqtt_pass = env("MQTT_PASSWORD")
    mqtt_prefix = env("MQTT_TOPIC_PREFIX", "meshcore")
    mqtt_iata = env("MQTT_IATA", "AUS")
    mqtt_tls = env_bool("MQTT_TLS", True)
    mqtt_transport = env("MQTT_TRANSPORT", "websockets")
    meshcore_timeout = float(env("MESHCORE_TIMEOUT", "30"))

    if not mqtt_host:
        log.error("MQTT_HOST is required")
        return 1

    if mode == "serial":
        if not serial_port:
            log.error("OBSERVER_MODE=serial requires SERIAL_PORT in .env "
                      "(use a stable /dev/serial/by-id/... path)")
            return 1
        log.info("Connecting directly to radio at %s @ %d baud (handshake timeout %.0fs)",
                 serial_port, serial_baud, meshcore_timeout)
        mc = await MeshCore.create_serial(serial_port, baudrate=serial_baud,
                                          default_timeout=meshcore_timeout)
        if mc is None:
            log.error("Radio did not respond to APPSTART within %.0fs on %s. "
                      "Check that the radio is plugged in, the device path is correct, "
                      "and no other process (meshcore-proxy, Companion app, mc-cli) is "
                      "holding the serial port.",
                      meshcore_timeout, serial_port)
            return 1
    elif mode == "tcp":
        log.info("Connecting to meshcore-proxy at %s:%d (handshake timeout %.0fs)",
                 proxy_host, proxy_port, meshcore_timeout)
        mc = await MeshCore.create_tcp(proxy_host, proxy_port,
                                       default_timeout=meshcore_timeout)
        if mc is None:
            log.error("Radio did not respond to APPSTART within %.0fs via proxy at %s:%d. "
                      "Check that meshcore-proxy is connected to a radio and that no other "
                      "client is monopolizing it. You can raise MESHCORE_TIMEOUT in .env, "
                      "or switch to OBSERVER_MODE=serial to bypass the proxy entirely.",
                      meshcore_timeout, proxy_host, proxy_port)
            return 1
    else:
        log.error("OBSERVER_MODE must be 'tcp' or 'serial' (got %r)", mode)
        return 1

    pubkey = mc.self_info.get("public_key", "") or ""
    if not pubkey:
        log.error("Could not read public_key from radio — aborting")
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
