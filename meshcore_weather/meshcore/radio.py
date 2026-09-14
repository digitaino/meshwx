"""Meshcore radio interface using the official meshcore Python library.

Uses meshcore_py to communicate with a Meshcore device over USB serial.
Listens for incoming channel messages and DMs, sends responses.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from meshcore import MeshCore, EventType

from meshcore_weather.config import settings
from meshcore_weather.mqtt import MqttPublisher

logger = logging.getLogger(__name__)

# How often to re-advertise and refresh contacts (seconds)
CONTACTS_REFRESH = 120  # 2 minutes


# Opening the USB serial port toggles DTR/RTS, which resets the ESP32 on
# Heltec-style boards. The node then boots for 2-3 s and never sees an
# APP_START sent right after open, so meshcore_py's create_serial() gives up
# after one try. Wait for the boot, then ask; ask again if it was still busy.
SERIAL_BOOT_DELAYS = (3.0, 3.0, 5.0)


async def _open_serial(port: str, baud: int) -> MeshCore | None:
    from meshcore.serial_cx import SerialConnection
    cx = SerialConnection(port, baud)
    mc = MeshCore(cx)
    await mc.dispatcher.start()
    if await mc.connection_manager.connect() is None:
        await mc.dispatcher.stop()
        raise ConnectionError(f"could not open {port}")
    for delay in SERIAL_BOOT_DELAYS:
        await asyncio.sleep(delay)
        try:
            res = await mc.commands.send_appstart()
        except Exception as e:
            logger.debug("APP_START attempt failed: %s", e)
            res = None
        if res is not None and res.type != EventType.ERROR:
            return mc
    await mc.disconnect()
    return None


def clean_text(value: str, max_len: int) -> str:
    """Strip control characters (log forging, terminal escapes) and cap the
    length of anything that arrived over the air before it is logged or
    handled. Names are 32 bytes by protocol, text is at most 160."""
    if not isinstance(value, str):
        value = str(value)
    value = "".join(c for c in value if c.isprintable() or c == " ")
    return value[:max_len]


class MeshcoreRadio:
    """Interface to a Meshcore radio device using the official library."""

    def __init__(self):
        self._mc: MeshCore | None = None
        self._running = False
        self._channel_idx: int | None = None
        self._data_channel_idx: int | None = None
        self._discover_channel_idx: int | None = None
        self._channel_handler: Callable | None = None
        self._discover_handler: Callable | None = None
        self._dm_handler: Callable | None = None
        self._advert_handler: Callable | None = None
        self._advert_task: asyncio.Task | None = None
        self._contacts_task: asyncio.Task | None = None
        self._mqtt: MqttPublisher | None = None
        self.last_advert_at: float = 0.0
        # Shared send lock — prevents the scheduler and on-demand
        # request handler from interleaving messages on the data channel.
        # Without this, a client DM triggering respond_to_data_request
        # while the scheduler is mid-tick sending radar chunks would
        # cause mixed messages on the wire.
        self.send_lock: asyncio.Lock = asyncio.Lock()

    def on_channel_message(self, handler: Callable) -> None:
        """Register handler: async def handler(channel, sender_name, text)"""
        self._channel_handler = handler

    # Keep old name for backwards compat during transition
    def on_message(self, handler: Callable) -> None:
        self._channel_handler = handler

    def on_advert(self, handler: Callable) -> None:
        """Register handler: async def handler(contact_name, pubkey_prefix)"""
        self._advert_handler = handler

    def on_discover_ping(self, handler: Callable) -> None:
        """Register handler: async def handler() — called when a ping arrives on discovery channel"""
        self._discover_handler = handler

    def on_dm(self, handler: Callable) -> None:
        """Register handler: async def handler(pubkey_prefix, sender_name, text)"""
        self._dm_handler = handler

    async def start(self) -> None:
        """Connect to Meshcore radio via serial or TCP."""
        port = settings.serial_port
        baud = settings.serial_baud

        if port.startswith("tcp://"):
            host_port = port[6:]
            host, tcp_port = host_port.rsplit(":", 1)
            logger.info("Connecting to Meshcore radio via TCP %s:%s", host, tcp_port)
            self._mc = await MeshCore.create_tcp(host, int(tcp_port))
        else:
            logger.info("Connecting to Meshcore radio on %s @ %d baud", port, baud)
            self._mc = await _open_serial(port, baud)
        if self._mc is None:
            # meshcore_py returns None when the node never answers APP_START:
            # wrong firmware (BLE-only companion, repeater), wrong baud, or
            # the ESP32 is held in reset by the port's DTR/RTS lines.
            raise ConnectionError(
                f"no companion response on {port} (is the firmware 'Companion Radio USB'?)")
        try:
            await self._configure_node()
        except Exception:
            # Leave nothing half-open: the retry loop opens the port again.
            try:
                await self._mc.disconnect()
            except Exception:
                pass
            self._mc = None
            self._running = False
            raise
        self._running = True

    async def _configure_node(self) -> None:
        """Resolve (or create) the bot's channels, subscribe, advertise."""
        # Text channel: create it if the node does not have it yet (a fresh
        # flash has only slot 0). Never slot 0: that is the public channel.
        try:
            self._channel_idx = await self._resolve_channel(settings.meshcore_channel)
        except ValueError:
            created = await self._create_channel(settings.meshcore_channel)
            if created is None:
                raise ConnectionError(f"could not create text channel {settings.meshcore_channel!r}: no free slot")
            self._channel_idx = created
            logger.info("Created text channel %d (%s)", created, settings.meshcore_channel)
        logger.info("Listening on channel %d (%s)", self._channel_idx, settings.meshcore_channel)

        # Resolve data channel for MeshWX binary protocol (if configured)
        if settings.meshwx_channel:
            try:
                self._data_channel_idx = await self._resolve_channel(settings.meshwx_channel)
                logger.info("Data channel %d (%s)", self._data_channel_idx, settings.meshwx_channel)
            except ValueError:
                # Channel doesn't exist — create it on a free slot
                created = await self._create_channel(settings.meshwx_channel)
                if created is not None:
                    self._data_channel_idx = created
                    logger.info("Created data channel %d (%s)", created, settings.meshwx_channel)
                else:
                    logger.warning("Could not create data channel '%s' — no free slots",
                                   settings.meshwx_channel)

        # Resolve discovery channel for beacon broadcasts
        if settings.meshwx_discover_channel:
            try:
                self._discover_channel_idx = await self._resolve_channel(settings.meshwx_discover_channel)
                logger.info("Discovery channel %d (%s)", self._discover_channel_idx, settings.meshwx_discover_channel)
            except ValueError:
                created = await self._create_channel(settings.meshwx_discover_channel)
                if created is not None:
                    self._discover_channel_idx = created
                    logger.info("Created discovery channel %d (%s)", created, settings.meshwx_discover_channel)
                else:
                    logger.warning("Could not create discovery channel '%s' — no free slots",
                                   settings.meshwx_discover_channel)

        # Subscribe to channel messages, DMs, and new adverts
        self._mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_msg)
        self._mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_dm)
        self._mc.subscribe(EventType.ADVERTISEMENT, self._on_advert)

        # Start auto-fetching messages from the device
        await self._mc.start_auto_message_fetching()

        # Node clock: GRP_TXT carries a sender timestamp that repeaters use to
        # dedupe, so a node with a dead clock repeats hashes. Sync it from us.
        try:
            await self._mc.commands.set_time(int(time.time()))
        except Exception:
            logger.debug("Could not set node time")

        # Ensure auto-add contacts is enabled so adverts create contacts
        try:
            await self._mc.commands.set_autoadd_config(1)
            logger.info("Auto-add contacts enabled")
        except Exception:
            logger.debug("Could not set auto-add config")

        # Auto-refresh contacts when adverts arrive
        self._mc.auto_update_contacts = True

        # Load contacts and advertise ourselves
        await self._mc.ensure_contacts()
        await self._send_advert()

        # Periodic tasks: re-advert and refresh contacts
        self._advert_task = asyncio.create_task(self._advert_loop())
        self._contacts_task = asyncio.create_task(self._contacts_loop())

        logger.info("Meshcore radio connected. Node: %s", self._mc.self_info.get("name") or self._mc.self_info.get("adv_name", "?"))
        logger.info("Radio TX: %s", "ENABLED" if settings.tx_enabled
                    else "DISABLED (receive-only passive observer)")

        # Optional MQTT publishing of raw RX packets (CoreScope etc).
        # Done last so any failure here cannot prevent radio startup.
        if settings.mqtt_enabled:
            try:
                pubkey = self._mc.self_info.get("public_key", "") or ""
                if pubkey:
                    self._mqtt = MqttPublisher(
                        host=settings.mqtt_host,
                        port=settings.mqtt_port,
                        topic_prefix=settings.mqtt_topic_prefix,
                        iata=settings.mqtt_iata,
                        pubkey=pubkey,
                        username=settings.mqtt_username,
                        password=settings.mqtt_password,
                        origin=settings.mqtt_origin,
                    )
                    self._mc.subscribe(EventType.RX_LOG_DATA, self._on_rx_log)
                    logger.info("MQTT publishing enabled for pubkey %s", pubkey[:12])
                else:
                    logger.warning("MQTT enabled but no pubkey from radio — skipping")
            except Exception:
                logger.exception("MQTT setup failed (non-fatal, bot continues)")

    async def _create_channel(self, channel_name: str) -> int | None:
        """Create a channel on the first free or reusable slot. Returns index or None."""
        slots: dict[int, str] = {}
        for i in range(8):
            try:
                ch = await self._mc.commands.get_channel(i)
                name = ch.payload.get("channel_name", "")
                slots[i] = name
            except Exception:
                break

        # First: check if there's a stale version of this channel (e.g. #name vs name)
        bare = channel_name.lstrip("#")
        for i, name in slots.items():
            if i == 0:
                continue
            if name.lstrip("#") == bare and name != channel_name:
                logger.info("Overwriting stale channel %d (%s -> %s)", i, name, channel_name)
                try:
                    result = await self._mc.commands.set_channel(i, channel_name)
                    if result.type == EventType.OK:
                        return i
                except Exception:
                    pass

        # Otherwise find a free slot
        for i in range(1, 8):
            if not slots.get(i):
                try:
                    result = await self._mc.commands.set_channel(i, channel_name)
                    if result.type == EventType.OK:
                        return i
                except Exception:
                    logger.debug("Failed to create channel on slot %d", i)
        return None

    async def _resolve_channel(self, channel_ref: str) -> int:
        try:
            return int(channel_ref)
        except ValueError:
            pass
        for i in range(8):
            try:
                ch = await self._mc.commands.get_channel(i)
                name = ch.payload.get("channel_name", "")
                if name == channel_ref:
                    return i
            except Exception:
                break
        raise ValueError(
            f"Channel '{channel_ref}' not found on this device. "
            f"Create it first or use a channel index (0-7)."
        )

    async def stop(self) -> None:
        self._running = False
        for task in (self._advert_task, self._contacts_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._mc:
            await self._mc.disconnect()
        if self._mqtt:
            self._mqtt.close()
        logger.info("Meshcore radio disconnected")

    # -- Sending --

    async def send_channel_message(self, channel: int, text: str) -> None:
        """Send a message on our dedicated channel. Never sends on ch 0."""
        if not settings.tx_enabled:
            logger.info("TX disabled — suppressed channel message on ch %s", channel)
            return
        if not self._mc:
            logger.error("Cannot send - not connected")
            return
        if channel == 0 or channel != self._channel_idx:
            logger.warning("Blocked send on ch %d (our ch is %d)", channel, self._channel_idx)
            return
        budget = self.channel_text_budget()
        if len(text) > budget:
            logger.warning("Channel text of %d chars exceeds the %d-char budget; clipping", len(text), budget)
            text = text[:budget]
        try:
            await self._mc.commands.send_chan_msg(channel, text)
            logger.info("Sent on ch %d (flood): %s", channel, text[:80])
        except Exception:
            logger.exception("Failed to send channel message")

    async def send_binary_channel(self, payload: bytes) -> None:
        """Send raw binary data on the MeshWX data channel.

        Acquires the shared send_lock so that scheduled broadcasts and
        on-demand request responses never interleave their messages.
        Without this, a client DM arriving mid-broadcast-tick would
        cause mixed message sequences on the wire (e.g., radar chunk 3
        followed by a forecast response followed by radar chunk 4).

        Bypasses send_chan_msg (which UTF-8 encodes) by constructing
        the channel message packet directly with raw bytes.
        """
        if not settings.tx_enabled:
            logger.info("TX disabled — suppressed %dB binary broadcast", len(payload))
            return
        if not self._mc or self._data_channel_idx is None:
            return
        async with self.send_lock:
            import time as _time
            ts_bytes = int(_time.time()).to_bytes(4, "little")
            data = (
                b"\x03\x00"
                + self._data_channel_idx.to_bytes(1, "little")
                + ts_bytes
                + payload
            )
            try:
                result = await self._mc.commands.send(data, [EventType.OK, EventType.ERROR])
                if result.type == EventType.ERROR:
                    logger.warning("Binary send failed on data ch %d: %s", self._data_channel_idx, result.payload)
                else:
                    logger.info("Sent ch%d: %dB",
                                self._data_channel_idx, len(payload))
            except Exception:
                logger.exception("Failed to send binary on data channel")

    async def send_beacon(self, payload: bytes) -> None:
        """Send a beacon on the discovery channel."""
        if not settings.tx_enabled:
            logger.info("TX disabled — suppressed discovery beacon")
            return
        if not self._mc or self._discover_channel_idx is None:
            return
        async with self.send_lock:
            import time as _time
            ts_bytes = int(_time.time()).to_bytes(4, "little")
            data = (
                b"\x03\x00"
                + self._discover_channel_idx.to_bytes(1, "little")
                + ts_bytes
                + payload
            )
            try:
                result = await self._mc.commands.send(data, [EventType.OK, EventType.ERROR])
                if result.type == EventType.ERROR:
                    logger.warning("Beacon send failed on ch %d", self._discover_channel_idx)
                else:
                    logger.debug("Beacon sent on discovery ch %d (%d bytes)",
                                 self._discover_channel_idx, len(payload))
            except Exception:
                logger.exception("Failed to send beacon")

    async def send_dm(self, pubkey_prefix: str, text: str) -> bool:
        """Send a direct message to a contact by their public key prefix."""
        if not settings.tx_enabled:
            logger.info("TX disabled — suppressed DM to %s", pubkey_prefix[:8])
            return False
        if not self._mc:
            logger.error("Cannot send DM - not connected")
            return False
        try:
            result = await self._mc.commands.send_msg(pubkey_prefix, text)
            if result.type == EventType.ERROR:
                logger.warning("DM to %s failed: %s", pubkey_prefix[:8], result.payload)
                return False
            logger.info("DM sent to %s: %s", pubkey_prefix[:8], text[:80])
            return True
        except Exception:
            logger.exception("Failed to send DM to %s", pubkey_prefix[:8])
            return False

    # -- Contact lookup --

    def find_contact_by_name(self, name: str) -> dict | None:
        """Look up a contact by advertised name. Returns contact dict or None."""
        if not self._mc:
            return None
        return self._mc.get_contact_by_name(name)

    def find_contact_by_key(self, pubkey_prefix: str) -> dict | None:
        """Look up a contact by public key prefix. Returns contact dict or None."""
        if not self._mc:
            return None
        return self._mc.get_contact_by_key_prefix(pubkey_prefix)

    # -- Event handlers --

    async def _on_channel_msg(self, event) -> None:
        payload = event.payload
        channel_idx = payload.get("channel_idx", 0)
        text = payload.get("text", "")

        if channel_idx == 0:
            return

        # Discovery channel — check for ping
        if channel_idx == self._discover_channel_idx and self._discover_handler:
            # Any message on the discovery channel triggers a beacon response
            logger.info("Discovery ping received on ch %d", channel_idx)
            try:
                await self._discover_handler()
            except Exception:
                logger.exception("Error in discovery handler")
            return

        # Text command channel or data channel (v4 clients send requests on data ch)
        if channel_idx != self._channel_idx and channel_idx != self._data_channel_idx:
            return

        sender = "unknown"
        if ": " in text:
            sender, text = text.split(": ", 1)
        sender = clean_text(sender, 40) or "unknown"
        text = clean_text(text, 200)

        hops = payload.get("path_len")
        hops = int(hops) if isinstance(hops, int) and hops >= 0 else None
        logger.info("Channel msg from %s on ch %d (%s): %s", sender, channel_idx,
                    f"{hops} hops" if hops is not None else "hops ?", text[:80])

        if self._channel_handler:
            try:
                await self._channel_handler(str(channel_idx), sender, text, hops)
            except Exception:
                logger.exception("Error in channel message handler")

    async def _on_dm(self, event) -> None:
        payload = event.payload
        pubkey_prefix = payload.get("pubkey_prefix", "")
        text = payload.get("text", "")

        # Resolve sender name from contacts
        sender_name = "unknown"
        contact = self.find_contact_by_key(pubkey_prefix)
        if contact:
            sender_name = clean_text(contact.get("adv_name", "unknown"), 40) or "unknown"
        text = clean_text(text, 200)
        pubkey_prefix = "".join(c for c in str(pubkey_prefix) if c in "0123456789abcdefABCDEF")[:64]

        logger.info("DM from %s (%s): %s", sender_name, pubkey_prefix[:8], text[:80])

        if self._dm_handler:
            try:
                await self._dm_handler(pubkey_prefix, sender_name, text)
            except Exception:
                logger.exception("Error in DM handler")

    async def _on_rx_log(self, event) -> None:
        """Forward a raw RX_LOG_DATA event to MQTT. Never raises."""
        if self._mqtt is None:
            return
        try:
            self._mqtt.publish_packet(event.payload)
        except Exception:
            logger.exception("MQTT publish failed (non-fatal)")

    async def _on_advert(self, event) -> None:
        """Handle an incoming advertisement from another node."""
        # Refresh contacts to pick up the new node
        try:
            await self._mc.ensure_contacts(follow=True)
        except Exception:
            pass

        if not self._advert_handler:
            return

        # Try to identify who just adverted
        # The event marks contacts dirty; after ensure_contacts we can check
        # We don't get the name directly from the event, but we can check
        # pending contacts
        pending = self._mc._pending_contacts
        for key, contact in list(pending.items()):
            name = clean_text(contact.get("adv_name", "unknown"), 40) or "unknown"
            prefix = str(key)[:12].lower()
            logger.info("New advert from %s (%s)", name, prefix)
            try:
                await self._advert_handler(name, prefix)
            except Exception:
                logger.exception("Error in advert handler")

    # -- Periodic tasks --

    async def _send_advert(self) -> None:
        """Advertise ourselves so other nodes can discover and DM us."""
        if not settings.tx_enabled:
            logger.info("TX disabled — suppressed advertisement")
            return
        try:
            await self._mc.commands.send_advert(flood=True)
            self.last_advert_at = time.time()
            logger.info("Sent advertisement (flood)")
        except Exception:
            logger.exception("Failed to send advert")

    async def advert_if_stale(self, max_age_s: int = 3600) -> bool:
        """Advert now unless one went out recently. Used when a stranger
        talks to us on the channel and we have no DM path back."""
        if time.time() - self.last_advert_at < max_age_s or not settings.tx_enabled or not self._mc:
            return False
        await self._send_advert()
        return True

    async def _advert_loop(self) -> None:
        while self._running:
            await asyncio.sleep(max(1, settings.advert_interval_hours) * 3600)
            await self._send_advert()
            # Refresh contacts right after advert to pick up new peers
            try:
                await self._mc.ensure_contacts(follow=True)
            except Exception:
                pass

    async def _contacts_loop(self) -> None:
        while self._running:
            await asyncio.sleep(CONTACTS_REFRESH)
            try:
                await self._mc.ensure_contacts(follow=True)
            except Exception:
                logger.debug("Failed to refresh contacts")

    # -- Management (portal) ---------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._mc is not None and self._running

    def _require(self) -> MeshCore:
        if not self.connected:
            raise ConnectionError("radio not connected")
        return self._mc

    async def info(self) -> dict:
        """Identity and radio parameters straight from the node, plus battery."""
        mc = self._require()
        si = dict(mc.self_info or {})
        out = {
            "name": si.get("name"),
            "public_key": si.get("public_key"),
            "radio_freq": si.get("radio_freq"),
            "radio_bw": si.get("radio_bw"),
            "radio_sf": si.get("radio_sf"),
            "radio_cr": si.get("radio_cr"),
            "tx_power": si.get("tx_power"),
            "max_tx_power": si.get("max_tx_power"),
            "adv_lat": si.get("adv_lat"),
            "adv_lon": si.get("adv_lon"),
            "adv_type": si.get("adv_type"),
            "manual_add_contacts": si.get("manual_add_contacts"),
            "battery_mv": None,
            "channels": {"text": self._channel_idx, "data": self._data_channel_idx,
                         "discover": self._discover_channel_idx},
        }
        try:
            bat = await mc.commands.get_bat()
            if bat.type == EventType.BATTERY:
                out["battery_mv"] = bat.payload.get("level")
        except Exception:
            pass
        return out

    async def list_channels(self) -> list[dict]:
        mc = self._require()
        chans = []
        for i in range(8):
            try:
                ch = await mc.commands.get_channel(i)
            except Exception:
                break
            if ch.type != EventType.CHANNEL_INFO:
                break
            name = ch.payload.get("channel_name", "") or ""
            secret = ch.payload.get("channel_secret", b"")
            if isinstance(secret, (bytes, bytearray)):
                secret = secret.hex()
            chans.append({"idx": i, "name": name, "secret": secret or "",
                          "role": self._role_for(i)})
        return chans

    def _role_for(self, idx: int) -> str | None:
        if idx == self._channel_idx:
            return "text"
        if idx == self._data_channel_idx:
            return "data"
        if idx == self._discover_channel_idx:
            return "discover"
        return None

    # Role -> (index attribute, settings attribute). The bot listens/sends on
    # these; the names in settings are what the operator configured.
    _ROLES = {
        "text": ("_channel_idx", "meshcore_channel"),
        "data": ("_data_channel_idx", "meshwx_channel"),
        "discover": ("_discover_channel_idx", "meshwx_discover_channel"),
    }

    async def _slots(self) -> dict[int, str]:
        mc = self._require()
        out: dict[int, str] = {}
        for i in range(8):
            try:
                ch = await mc.commands.get_channel(i)
            except Exception:
                break
            if ch.type != EventType.CHANNEL_INFO:
                break
            out[i] = ch.payload.get("channel_name", "") or ""
        return out

    async def set_channel_name(self, idx: int, name: str, secret_hex: str | None = None) -> str | None:
        """Rename slot `idx` on the node. If the slot carries one of the bot's
        roles, the role follows the new name (settings updated in memory;
        the caller persists .env). Returns the role, or None."""
        mc = self._require()
        if not 0 <= idx <= 7:
            raise ValueError("channel index must be 0-7")
        if idx == 0:
            raise ValueError("slot 0 is the public channel and is left alone")
        secret = bytes.fromhex(secret_hex) if secret_hex else None
        res = await mc.commands.set_channel(idx, name, secret)
        if res.type != EventType.OK:
            raise RuntimeError(f"radio refused set_channel: {res.payload}")
        role = self._role_for(idx)
        if role and name:
            setattr(settings, self._ROLES[role][1], name)
        return role

    async def clear_channel(self, idx: int) -> None:
        if idx == 0:
            raise ValueError("channel 0 (public) cannot be cleared")
        role = self._role_for(idx)
        if role:
            raise ValueError(f"slot {idx} is the bot's {role} channel; change it under Text Bot instead of clearing it")
        await self.set_channel_name(idx, "", "00" * 16)

    async def assign_role(self, role: str, name: str) -> int | None:
        """Point a bot role at a channel name, live: reuse a slot that already
        has that name, else rename the role's current slot in place, else
        create it on a free slot. Empty name detaches the role (data/discover
        only). Returns the slot index."""
        if role not in self._ROLES:
            raise ValueError(f"unknown role {role!r}")
        idx_attr, set_attr = self._ROLES[role]
        name = name.strip()
        if not name:
            if role == "text":
                raise ValueError("the text channel is required")
            setattr(self, idx_attr, None)
            setattr(settings, set_attr, "")
            return None
        if not name.startswith("#") and not name.isdigit():
            raise ValueError("channel names start with '#'")
        slots = await self._slots()
        current = getattr(self, idx_attr)
        target = next((i for i, n in slots.items() if n == name and i != 0), None)
        if target is None:
            other_roles = {getattr(self, a) for r, (a, _) in self._ROLES.items() if r != role}
            if current is not None and current not in other_roles and current != 0:
                target = current                      # rename in place, keep the slot
            else:
                target = next((i for i in range(1, 8) if not slots.get(i)), None)
                if target is None:
                    raise RuntimeError("no free channel slot on the node")
            res = await self._require().commands.set_channel(target, name)
            if res.type != EventType.OK:
                raise RuntimeError(f"radio refused set_channel: {res.payload}")
        setattr(self, idx_attr, target)
        setattr(settings, set_attr, name)
        logger.info("Channel role %s -> slot %d (%s)", role, target, name)
        return target

    async def set_name(self, name: str) -> None:
        mc = self._require()
        name = name.strip()
        if not name or len(name.encode()) > 31:
            raise ValueError("name must be 1-31 bytes")
        res = await mc.commands.set_name(name)
        if res.type != EventType.OK:
            raise RuntimeError(f"radio refused set_name: {res.payload}")
        mc.self_info["name"] = name

    async def set_radio_params(self, freq_mhz: float, bw_khz: float, sf: int, cr: int) -> None:
        mc = self._require()
        if not (400 <= freq_mhz <= 1000):
            raise ValueError("frequency must be 400-1000 MHz")
        if bw_khz not in (7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500):
            raise ValueError("bandwidth must be a LoRa bandwidth in kHz (62.5, 125, 250, 500 ...)")
        if not (5 <= sf <= 12) or not (5 <= cr <= 8):
            raise ValueError("sf must be 5-12 and cr 5-8")
        res = await mc.commands.set_radio(freq_mhz, bw_khz, sf, cr)
        if res.type != EventType.OK:
            raise RuntimeError(f"radio refused set_radio: {res.payload}")
        mc.self_info.update({"radio_freq": freq_mhz, "radio_bw": bw_khz, "radio_sf": sf, "radio_cr": cr})

    async def set_tx_power(self, dbm: int) -> None:
        mc = self._require()
        mx = (mc.self_info or {}).get("max_tx_power") or 30
        if not (0 <= dbm <= mx):
            raise ValueError(f"tx power must be 0-{mx} dBm")
        res = await mc.commands.set_tx_power(dbm)
        if res.type != EventType.OK:
            raise RuntimeError(f"radio refused set_tx_power: {res.payload}")
        mc.self_info["tx_power"] = dbm

    async def set_coords(self, lat: float, lon: float) -> None:
        mc = self._require()
        res = await mc.commands.set_coords(lat, lon)
        if res.type != EventType.OK:
            raise RuntimeError(f"radio refused set_coords: {res.payload}")
        mc.self_info.update({"adv_lat": lat, "adv_lon": lon})

    async def advert_now(self, flood: bool = True) -> bool:
        """Operator-requested advert. Honours the TX switch."""
        if not settings.tx_enabled:
            return False
        mc = self._require()
        res = await mc.commands.send_advert(flood=flood)
        ok = res.type == EventType.OK
        if ok:
            self.last_advert_at = time.time()
        logger.info("Sent advertisement (%s, requested from the portal)" if ok else "Advert refused by node: %s",
                    "flood" if flood else "direct" if ok else res.payload)
        return ok

    async def reboot(self) -> None:
        mc = self._require()
        try:
            await mc.commands.reboot()
        except Exception:
            pass   # the node drops the link mid-command

    def peer_bots(self) -> list[dict]:
        """Other weather bots we have heard adverts from: contacts whose name
        starts with the peer prefix and that carry coordinates."""
        if not self._mc:
            return []
        prefix = settings.peer_bot_prefix.upper()
        me = (self._mc.self_info or {}).get("public_key", "")
        out = []
        for key, c in (self._mc.contacts or {}).items():
            name = (c.get("adv_name") or "")
            if not name.upper().startswith(prefix) or key == me:
                continue
            lat, lon = c.get("adv_lat"), c.get("adv_lon")
            if not lat and not lon:
                continue
            out.append({"name": name, "public_key": key, "lat": float(lat), "lon": float(lon)})
        return out

    async def contacts(self) -> list[dict]:
        mc = self._require()
        out = []
        for key, c in (mc.contacts or {}).items():
            out.append({
                "public_key": key if isinstance(key, str) else str(key),
                "name": c.get("adv_name"),
                "type": c.get("type"),
                "last_advert": c.get("last_advert"),
                "lat": c.get("adv_lat"), "lon": c.get("adv_lon"),
                "out_path_len": c.get("out_path_len"),
            })
        out.sort(key=lambda c: c.get("last_advert") or 0, reverse=True)
        return out

    async def stats(self) -> dict:
        mc = self._require()
        out: dict = {}
        for label, fn, ev in (("core", mc.commands.get_stats_core, EventType.STATS_CORE),
                              ("radio", mc.commands.get_stats_radio, EventType.STATS_RADIO),
                              ("packets", mc.commands.get_stats_packets, EventType.STATS_PACKETS)):
            try:
                res = await fn()
                out[label] = dict(res.payload) if res.type == ev else None
            except Exception:
                out[label] = None
        return out

    def channel_text_budget(self) -> int:
        """Characters of text that survive in one channel message.

        Firmware (BaseChatMesh.cpp): plaintext = timestamp(4) + txt_type(1)
        + "<name>: " + text, clipped at 160 bytes. So the text gets
        160 - 5 - len(name) - 2 = 153 - len(name): 147 for "WX-AUS". A DM
        has no name (timestamp + flags + text, 160) so its budget is 155;
        replies are rendered to 147 and fit either way.
        """
        name = ((self._mc.self_info if self._mc else None) or {}).get("name") or "WX-XXX"
        return max(100, 153 - len(name.encode()))

    @property
    def public_key(self) -> str:
        return ((self._mc.self_info if self._mc else None) or {}).get("public_key", "") or ""

    @property
    def channel_idx(self) -> int | None:
        return self._channel_idx

    @property
    def data_channel_idx(self) -> int | None:
        return self._data_channel_idx

    @property
    def discover_channel_idx(self) -> int | None:
        return self._discover_channel_idx
