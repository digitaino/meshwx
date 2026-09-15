"""Meshcore radio interface using the official meshcore Python library.

Uses meshcore_py to communicate with a Meshcore device over USB serial.
Listens for incoming channel messages and DMs, sends responses.
"""

import asyncio
import glob
import logging
import os
import time
from collections.abc import Callable, Coroutine
from typing import Any

from meshcore import MeshCore, EventType

from meshcore_weather.config import settings
from meshcore_weather.meshcore import profile
from meshcore_weather.meshcore.delivery import (
    PAYLOAD_GRP_DATA,
    Outbound,
    build_channel_data_payload,
    build_channel_payload,
    delivery_tracker,
    packet_hash,
)
from meshcore_weather.mqtt import MqttPublisher

logger = logging.getLogger(__name__)

# How often to re-advertise and refresh contacts (seconds)
CONTACTS_REFRESH = 120  # 2 minutes

# Companion protocol: CMD_SEND_CHANNEL_DATA and the "flood, no path" marker
# (firmware v1.17.1, examples/companion_radio/MyMesh.cpp).
CMD_SEND_CHANNEL_DATA = 62
PATH_FLOOD = 0xFF
# The firmware clips a channel datagram's data at this many bytes.
MAX_CHANNEL_DATA = 165


# Opening the USB serial port toggles DTR/RTS, which resets the ESP32 on
# Heltec-style boards. The node then boots for 2-3 s and never sees an
# APP_START sent right after open, so meshcore_py's create_serial() gives up
# after one try. Wait for the boot, then ask; ask again if it was still busy.
SERIAL_BOOT_DELAYS = (3.0, 3.0, 5.0)
# A node takes this long to come back after CMD_REBOOT before the port answers.
REBOOT_WAIT_S = 6.0
# Link test datagram: a data_type no app decodes (apps ignore anything but
# 0xFF10), so a test packet on #meshwx bothers nobody.
TEST_DATA_TYPE = 0xFF1E

# Adoption bookkeeping across reconnects: the radio object is rebuilt on a
# reconnect, so what was written onto a node and awaits verification, and
# how many times a node was tried, live at module level.
_ADOPT_ATTEMPTS: dict[str, int] = {}
_PENDING_VERIFY: dict | None = None
MAX_ADOPT_ATTEMPTS = 2


def candidate_ports(configured: str) -> list[str]:
    """Serial ports worth trying, the configured one first: the udev alias
    (deploy/99-meshcore-radio.rules), then every USB serial bridge on the
    host. A replacement board with a different USB chip shows up under a
    new name; the bot finds it instead of waiting for someone to edit .env.
    Paths that are the same device (a by-id symlink and its target) are
    tried once."""
    names = [configured, "/dev/meshcore"]
    for pattern in ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*", "/dev/cu.usb*"):
        names.extend(sorted(glob.glob(pattern)))
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name.startswith("tcp://") or not os.path.exists(name):
            continue
        real = os.path.realpath(name)
        if real in seen:
            continue
        seen.add(real)
        out.append(name)
    return out


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


CONTACT_TYPE_NAMES = {1: "client", 2: "repeater", 3: "room", 4: "sensor"}

# Companion firmware autoadd_config bits (MyMesh.cpp, v1.17.1). The type
# bits only take effect when manual_add_contacts is set.
AUTOADD_OVERWRITE_OLDEST = 0x01
AUTOADD_CHAT = 0x02
AUTOADD_REPEATER = 0x04
AUTOADD_ROOM = 0x08
AUTOADD_SENSOR = 0x10


def plan_contact_removals(contacts: dict, slots: int, keep_free: int, admin_key: str,
                          peer_prefix: str, own_key: str = "") -> list[tuple[str, str, str]]:
    """Which contacts to drop, as (public_key, name, reason). Pure, so the
    policy is testable: every non-client goes; then, if the people left
    would leave fewer than `keep_free` empty slots, the people heard longest
    ago (lastmod) go too. The admin, peer bots and ourselves are protected."""
    admin = (admin_key or "").lower().strip()
    prefix = (peer_prefix or "").upper()
    out: list[tuple[str, str, str]] = []
    people: list[tuple[float, str, str]] = []
    for key, c in contacts.items():
        key = str(key)
        name = c.get("adv_name") or "?"
        if own_key and key.lower() == own_key.lower():
            continue
        protected = (admin and key.lower().startswith(admin)) or (prefix and name.upper().startswith(prefix))
        ctype = c.get("type")
        if ctype != 1:
            if not protected:
                out.append((key, name, f"{CONTACT_TYPE_NAMES.get(ctype, f'type {ctype}')} takes a slot and cannot be DMed"))
            continue
        if not protected:
            people.append((float(c.get("lastmod") or 0), key, name))
    limit = max(1, slots - max(0, keep_free))
    protected_people = sum(1 for c in contacts.values() if c.get("type") == 1) - len(people)
    excess = protected_people + len(people) - limit
    if excess > 0:
        people.sort()
        for lastmod, key, name in people[:excess]:
            age_h = (time.time() - lastmod) / 3600 if lastmod else None
            out.append((key, name, "heard longest ago" + (f" ({age_h:.0f} h)" if age_h is not None else "") + ", making room"))
    return out


class MeshcoreRadio:
    """Interface to a Meshcore radio device using the official library."""

    def __init__(self):
        self._mc: MeshCore | None = None
        self._running = False
        self._channel_idx: int | None = None
        self._data_channel_idx: int | None = None
        self._channel_handler: Callable | None = None
        self._dm_handler: Callable | None = None
        self._advert_handler: Callable | None = None
        self._advert_task: asyncio.Task | None = None
        self._contacts_task: asyncio.Task | None = None
        self._mqtt: MqttPublisher | None = None
        self.last_advert_at: float = 0.0
        self.device: dict = {}                 # DEVICE_INFO: firmware build, model, capacity
        self.max_contacts: int = settings.contact_slots
        self.port: str | None = None           # the port the node actually answered on
        self._port_real: str | None = None     # its device node, to notice an unplug
        self._disconnect_handler: Callable | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._lost = False
        self.pending_adoption: dict | None = None   # a radio that is not the node in the profile
        self.adoption: dict | None = None      # the last adoption attempt (ok, steps, note)
        self.profile_note: str | None = None   # why the profile could not be refreshed, if so
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

    _discover_deprecation_logged = False

    def on_dm(self, handler: Callable) -> None:
        """Register handler: async def handler(pubkey_prefix, sender_name, text)"""
        self._dm_handler = handler

    def on_disconnect(self, handler: Callable) -> None:
        """Register handler: async def handler(reason). Called once when the
        link to the node is lost (USB unplugged, node dead, port gone)."""
        self._disconnect_handler = handler

    async def start(self) -> None:
        """Connect to Meshcore radio via serial or TCP."""
        try:
            while True:
                self._mc = await self._open_any()
                await self._query_device()
                if not await self._identity_check():
                    break
                # The node was adopted and rebooted: open it again, and the
                # next check records whether it now reports the profile's key.
            await self._configure_node()
            await self._snapshot_profile()
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

    async def _open_any(self) -> MeshCore:
        """Open the configured port, or the first port on which a companion
        answers when that one is missing or silent."""
        port = settings.serial_port
        baud = settings.serial_baud
        if port.startswith("tcp://"):
            host, tcp_port = port[6:].rsplit(":", 1)
            logger.info("Connecting to Meshcore radio via TCP %s:%s", host, tcp_port)
            mc = await MeshCore.create_tcp(host, int(tcp_port))
            if mc is None:
                raise ConnectionError(f"no companion response on {port}")
            self.port = port
            return mc
        tried: list[str] = []
        for p in candidate_ports(port) or [port]:
            logger.info("Connecting to Meshcore radio on %s @ %d baud", p, baud)
            try:
                mc = await _open_serial(p, baud)
            except Exception as e:
                tried.append(f"{p}: {e}")
                continue
            if mc is None:
                # meshcore_py returns None when the node never answers APP_START:
                # wrong firmware (BLE-only companion, repeater), wrong baud, or
                # the ESP32 is held in reset by the port's DTR/RTS lines.
                tried.append(f"{p}: no companion response")
                continue
            self.port = p
            self._port_real = os.path.realpath(p)
            if p != port:
                logger.warning("Radio answered on %s, not on the configured %s; update MCW_SERIAL_PORT "
                               "under System > Settings (or install the udev rule for /dev/meshcore)", p, port)
            return mc
        raise ConnectionError(
            f"no companion response on {port} (is the firmware 'Companion Radio USB'?)"
            + (f"; also tried {', '.join(t for t in tried if not t.startswith(port + ':'))}"
               if len(tried) > 1 else ""))

    async def _query_device(self) -> None:
        """What the node knows about itself: firmware build, model, contact capacity."""
        try:
            dq = await self._mc.commands.send_device_query()
            if dq.type == EventType.DEVICE_INFO:
                self.device = dict(dq.payload)
                if self.device.get("max_contacts"):
                    self.max_contacts = int(self.device["max_contacts"])
                logger.info("Node firmware %s (%s), %s contact slots", self.device.get("ver"),
                            self.device.get("fw_build"), self.max_contacts)
        except Exception:
            logger.debug("Device query failed")

    # -- Identity: the node profile ---------------------------------------------
    #
    # A replacement radio has a new key pair, and the key is the bot to every
    # phone and to the app. The profile (meshcore/profile.py) holds the key
    # and the settings; a radio that reports another key is written over
    # with them ("adopted"), rebooted and checked.

    async def _identity_check(self) -> bool:
        """Compare the node with the profile. Returns True when the node was
        just adopted and rebooted (the link is closed; open it again)."""
        global _PENDING_VERIFY
        prof = profile.load()
        si = dict(self._mc.self_info or {})
        key = si.get("public_key") or ""
        if _PENDING_VERIFY is not None:
            # A node was written over before this connect: did it take?
            pend, _PENDING_VERIFY = _PENDING_VERIFY, None
            ok = bool(prof) and key == prof.get("public_key")
            note = "" if ok else f"after the import the node reports {key[:8]}…, not the profile's key"
            if prof:
                profile.record_adoption(prof, from_key=pend.get("from_key"), model=self.device.get("model"),
                                        fw=self.device.get("ver"), steps=pend.get("steps", []), ok=ok, note=note)
                profile.save(prof)
            self.adoption = {"t": time.time(), "ok": ok, "steps": pend.get("steps", []), "note": note,
                             "from_key": pend.get("from_key")}
            if ok:
                logger.warning("Radio adopted: %s (%s) now runs as %s (%s…)", self.device.get("model"),
                               self.device.get("ver"), prof.get("name"), key[:8])
            else:
                logger.error("Radio adoption failed: %s", note)
        if not profile.differs(prof, si):
            self.pending_adoption = None
            return False
        why = profile.can_adopt(prof)
        self.pending_adoption = {
            "profile": profile.public_summary(prof),
            "radio": {"name": si.get("name"), "public_key": key, "model": self.device.get("model"),
                      "fw": self.device.get("ver")},
            "mode": settings.radio_adopt, "why_not": why,
            "attempts": _ADOPT_ATTEMPTS.get(key, 0),
        }
        if settings.radio_adopt != "auto" or why or _ADOPT_ATTEMPTS.get(key, 0) >= MAX_ADOPT_ATTEMPTS:
            logger.warning("Radio %s (%s…) is not the node in the profile (%s, %s…): running with its own "
                           "identity. %s", si.get("name"), key[:8], prof.get("name"), prof["public_key"][:8],
                           why or ("adopt it from Radio > Hardware" if settings.radio_adopt != "auto"
                                   else "adoption already failed; see Radio > Hardware"))
            return False
        _ADOPT_ATTEMPTS[key] = _ADOPT_ATTEMPTS.get(key, 0) + 1
        await self.adopt_profile(prof)
        return True

    async def adopt_profile(self, prof: dict | None = None) -> list[str]:
        """Write the profile onto the connected node and reboot it. The link
        is closed afterwards; the caller opens it again (start() does, the
        portal reconnects) and the next identity check records the result."""
        global _PENDING_VERIFY
        prof = prof or profile.load()
        why = profile.can_adopt(prof)
        if why:
            raise ValueError(why)
        si = dict(self._mc.self_info or {}) if self._mc else {}
        if not profile.differs(prof, si):
            raise ValueError("this radio already is the node in the profile")
        from_key = si.get("public_key")
        logger.warning("Adopting radio %s (%s…, %s) as %s (%s…)", si.get("name"), (from_key or "")[:8],
                       self.device.get("model") or "?", prof.get("name"), prof["public_key"][:8])
        try:
            steps = await profile.adopt(self._mc, prof)
        except Exception as e:
            profile.record_adoption(prof, from_key=from_key, model=self.device.get("model"),
                                    fw=self.device.get("ver"), steps=[], ok=False, note=str(e))
            profile.save(prof)
            self.adoption = {"t": time.time(), "ok": False, "steps": [], "note": str(e), "from_key": from_key}
            raise
        logger.info("Adoption written: %s; rebooting the node", "; ".join(steps))
        _PENDING_VERIFY = {"from_key": from_key, "steps": steps}
        try:
            await self._mc.commands.reboot()
        except Exception:
            pass
        try:
            await self._mc.disconnect()
        except Exception:
            pass
        self._mc = None
        await asyncio.sleep(REBOOT_WAIT_S)
        return steps

    async def _snapshot_profile(self) -> None:
        """Refresh the profile from the node we are running (never from a
        radio that is not the profile's node: that would lose the identity)."""
        if not self._mc:
            return
        prof = profile.load()
        si = dict(self._mc.self_info or {})
        if profile.differs(prof, si):
            self.profile_note = "not refreshed: this radio is not the node in the profile"
            return
        try:
            new = await profile.snapshot(self._mc, self.device, previous=prof)
            profile.save(new)
            self.profile_note = None if new.get("private_key") else f"saved without the key: {new.get('key_export')}"
            logger.info("Node profile saved: %s, %d contacts%s", new.get("name"), len(new.get("contacts") or []),
                        "" if new.get("private_key") else f" (no key: {new.get('key_export')})")
        except Exception as e:
            self.profile_note = f"could not save: {e}"
            logger.warning("Node profile not saved: %s", e)

    async def save_profile_now(self, force: bool = False) -> dict:
        """Portal: snapshot now. `force` makes the connected radio the
        profile's node even when the profile names another one."""
        mc = self._require()
        prof = profile.load()
        if profile.differs(prof, mc.self_info or {}) and not force:
            raise ValueError("this radio is not the node in the profile; adopt it, or force a new profile")
        if force:
            prof = None
        new = await profile.snapshot(mc, self.device, previous=prof)
        profile.save(new)
        self.pending_adoption = None
        self.profile_note = None if new.get("private_key") else f"saved without the key: {new.get('key_export')}"
        return profile.public_summary(new) or {}

    def profile_status(self) -> dict:
        prof = profile.load()
        si = dict((self._mc.self_info if self._mc else None) or {})
        return {
            "path": str(profile.PROFILE_PATH),
            "profile": profile.public_summary(prof),
            "matches": bool(prof) and not profile.differs(prof, si) if si else None,
            "mode": settings.radio_adopt,
            "pending": self.pending_adoption,
            "last_adoption": self.adoption,
            "note": self.profile_note,
            "port": {"configured": settings.serial_port, "actual": self.port},
        }

    async def test_transmit(self) -> dict:
        """Send one small datagram nobody decodes and report whether a
        repeater echoed it: the quickest answer to "is this radio getting
        out?"."""
        from meshcore_weather.traffic import traffic_log
        if not settings.tx_enabled:
            raise ValueError("transmit is off")
        if self._data_channel_idx is None:
            raise ValueError("no data channel on the node")
        payload = b"WXT" + os.urandom(3)
        ev = traffic_log.record("link_test", text=f"link test {payload[3:].hex()}")
        if not await self.send_channel_data(payload, data_type=TEST_DATA_TYPE, ev=ev):
            raise RuntimeError("the node did not accept the packet")
        deadline = time.time() + settings.echo_window_s * (settings.retransmit_max + 1) + 8
        while time.time() < deadline and not ev.get("delivery"):
            await asyncio.sleep(0.2)
        d = ev.get("delivery") or {}
        return {"sent": True, "bytes": len(payload), "heard": bool(d.get("echo")),
                "result": d.get("result") or "pending", "echo_ms": d.get("echo_ms"), "via": d.get("via"),
                "snr": d.get("snr"), "attempts": d.get("attempts"), "skipped": d.get("skipped")}

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

        # Data channel for the MeshWX binary datagrams. In v5 this is the
        # same channel as the text one (#meshwx carries both), so when the
        # names match, reuse the slot instead of burning a second one.
        if settings.meshwx_channel:
            if settings.meshwx_channel == settings.meshcore_channel:
                self._data_channel_idx = self._channel_idx
                logger.info("Data channel %d (%s, shared with text)",
                            self._data_channel_idx, settings.meshwx_channel)
            else:
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

        # Subscribe to channel messages, DMs, and new adverts
        self._mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_msg)
        self._mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_dm)
        self._mc.subscribe(EventType.ADVERTISEMENT, self._on_advert)       # a node we know adverted again
        self._mc.subscribe(EventType.NEW_CONTACT, self._on_new_contact)     # a node we did not know
        # Every raw packet the node hears (echoes of our own included) and
        # every DM ack: the delivery tracker decides whether to send again.
        self._mc.subscribe(EventType.RX_LOG_DATA, self._on_rx_log)
        self._mc.subscribe(EventType.ACK, self._on_ack)
        # The serial layer reports a closed port (USB pulled, node reset by
        # something else); without this the bot would keep talking to a link
        # that answers nothing, as it did the first time a radio was swapped.
        self._mc.subscribe(EventType.DISCONNECTED, self._on_disconnected)
        self._channel_secrets = {}

        # Start auto-fetching messages from the device
        await self._mc.start_auto_message_fetching()

        # Node clock: GRP_TXT carries a sender timestamp that repeaters use to
        # dedupe, so a node with a dead clock repeats hashes. Sync it from us.
        try:
            await self._mc.commands.set_time(int(time.time()))
        except Exception:
            logger.debug("Could not set node time")

        # Contact policy on the node itself. The firmware only consults the
        # per-type auto-add bits when manual-add mode is on, so:
        #   housekeeping on : manual mode + (overwrite oldest | companions):
        #                     repeaters, rooms and sensors are never stored
        #   housekeeping off: everything auto-adds, oldest overwritten when full
        try:
            if settings.contact_housekeeping:
                await self._mc.commands.set_autoadd_config(AUTOADD_OVERWRITE_OLDEST | AUTOADD_CHAT)
                await self._mc.commands.set_manual_add_contacts(True)
                logger.info("Node stores companion contacts only (overwrite oldest when full)")
            else:
                await self._mc.commands.set_autoadd_config(AUTOADD_OVERWRITE_OLDEST)
                await self._mc.commands.set_manual_add_contacts(False)
                logger.info("Node auto-adds every contact (overwrite oldest when full)")
            # self_info is a snapshot from APP_START; take a fresh one so the
            # portal shows the policy the node is actually running.
            await self._mc.commands.send_appstart()
        except Exception:
            logger.debug("Could not set contact auto-add policy")

        # Auto-refresh contacts when adverts arrive
        self._mc.auto_update_contacts = True

        # Load contacts, make room for people, advertise ourselves. A radio
        # that is not the profile's node keeps quiet about itself until the
        # operator adopts it or starts a new profile: an advert would hand
        # every phone a stranger's key under the bot's name.
        await self._mc.ensure_contacts()
        await self.housekeep_contacts()
        if self.pending_adoption:
            logger.warning("Advert held: this radio is not the node in the profile (adopt it under Radio > Hardware)")
        else:
            await self._send_advert()

        # Periodic tasks: re-advert, refresh contacts, watch the link
        self._advert_task = asyncio.create_task(self._advert_loop())
        self._contacts_task = asyncio.create_task(self._contacts_loop())
        self._watchdog_task = asyncio.create_task(self._link_watchdog())

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
        for task in (self._advert_task, self._contacts_task, self._watchdog_task):
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

    async def send_channel_message(self, channel: int, text: str, ev: dict | None = None) -> None:
        """Send a message on our dedicated channel (never on ch 0) and watch
        for a repeater's echo; without one it goes out once more, byte for
        byte the same, so nobody sees it twice (see delivery.py)."""
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
        ts = int(time.time())
        ts_bytes = ts.to_bytes(4, "little")
        secret = await self._channel_secret(channel)
        name = self._mc.self_info.get("name") or ""
        h = packet_hash(5, build_channel_payload(secret, name, text, ts)) if secret and name else None
        try:
            await self._mc.commands.send_chan_msg(channel, text, timestamp=ts_bytes)
            logger.info("Sent on ch %d (flood): %s", channel, text[:80])
        except Exception:
            logger.exception("Failed to send channel message")
            return

        async def resend(attempt: int) -> bool:
            if not settings.tx_enabled or not self._mc:
                return False
            await self._mc.commands.send_chan_msg(channel, text, timestamp=ts_bytes)
            logger.info("No echo heard: sent again on ch %d (attempt %d): %s", channel, attempt + 1, text[:60])
            return True

        delivery_tracker.track(Outbound(kind="channel_text", hash=h, resend=resend, ev=ev,
                                        window_s=settings.echo_window_s))

    async def send_channel_data(self, data: bytes, data_type: int = 0xFF10,
                                ev: dict | None = None) -> bool:
        """Flood one MeshCore GRP_DATA datagram on the data channel.

        meshcore-py has no helper for CMD_SEND_CHANNEL_DATA, so the frame is
        built by hand exactly as the companion firmware expects it
        (v1.17.1, examples/companion_radio/MyMesh.cpp):

            [62][channel idx u8][path_len u8 = 0xFF for flood][data_type u16 LE][data]

        Like a channel text message, it is tracked for a repeater's echo and
        sent once more byte-for-byte when none is heard (see delivery.py).
        """
        data = bytes(data)
        if len(data) > MAX_CHANNEL_DATA:
            logger.error("Channel data of %d bytes exceeds the %d-byte limit; not sent",
                         len(data), MAX_CHANNEL_DATA)
            return False
        if not settings.tx_enabled:
            logger.info("TX disabled — suppressed %dB data datagram (type 0x%04X)", len(data), data_type)
            return False
        idx = self._data_channel_idx
        if not self._mc or idx is None:
            logger.warning("Cannot send channel data: no data channel")
            return False
        if idx == 0:
            logger.warning("Blocked data send on ch 0 (the public channel)")
            return False
        frame = bytes([CMD_SEND_CHANNEL_DATA, idx, PATH_FLOOD]) + data_type.to_bytes(2, "little") + data
        secret = await self._channel_secret(idx)
        h = packet_hash(PAYLOAD_GRP_DATA, build_channel_data_payload(secret, data_type, data)) if secret else None
        async with self.send_lock:
            try:
                result = await self._mc.commands.send(frame, [EventType.OK, EventType.ERROR])
            except Exception:
                logger.exception("Failed to send data on ch %d", idx)
                return False
            if result.type == EventType.ERROR:
                logger.warning("Data send failed on ch %d: %s", idx, result.payload)
                return False
        logger.info("Sent data on ch %d: %d bytes (type 0x%04X)", idx, len(data), data_type)

        async def resend(attempt: int) -> bool:
            if not settings.tx_enabled or not self._mc:
                return False
            async with self.send_lock:
                res = await self._mc.commands.send(frame, [EventType.OK, EventType.ERROR])
            if res.type == EventType.ERROR:
                return False
            logger.info("No echo heard: sent data again on ch %d (attempt %d, %d bytes)",
                        idx, attempt + 1, len(data))
            return True

        delivery_tracker.track(Outbound(kind="channel_data", hash=h, resend=resend, ev=ev,
                                        window_s=settings.echo_window_s, ptype=PAYLOAD_GRP_DATA))
        return True

    _beacon_deprecation_logged = False

    async def send_dm(self, pubkey_prefix: str, text: str, ev: dict | None = None) -> bool:
        """Send a direct message to a contact by their public key prefix and
        wait for the recipient's ACK; without one it is sent again once, and
        a direct path that fails twice is reset to flood for next time."""
        if not settings.tx_enabled:
            logger.info("TX disabled — suppressed DM to %s", pubkey_prefix[:8])
            return False
        if not self._mc:
            logger.error("Cannot send DM - not connected")
            return False
        ts = int(time.time())
        try:
            result = await self._mc.commands.send_msg(pubkey_prefix, text, timestamp=ts)
            if result.type == EventType.ERROR:
                logger.warning("DM to %s failed: %s", pubkey_prefix[:8], result.payload)
                return False
            logger.info("DM sent to %s: %s", pubkey_prefix[:8], text[:80])
        except Exception:
            logger.exception("Failed to send DM to %s", pubkey_prefix[:8])
            return False
        self._track_dm(pubkey_prefix, text, ts, result.payload or {}, ev)
        return True

    def _track_dm(self, pubkey_prefix: str, text: str, ts: int, sent: dict, ev: dict | None) -> None:
        ack = sent.get("expected_ack")
        ack = ack.hex() if isinstance(ack, (bytes, bytearray)) else (ack or None)
        if not ack:
            return
        window = min(30.0, max(3.0, sent.get("suggested_timeout", 4000) / 1000 * 1.2))

        async def resend(attempt: int):
            if not settings.tx_enabled or not self._mc:
                return False
            res = await self._mc.commands.send_msg(pubkey_prefix, text, timestamp=ts, attempt=attempt)
            if res.type == EventType.ERROR:
                return False
            logger.info("No ACK from %s: DM sent again (attempt %d)", pubkey_prefix[:8], attempt + 1)
            code = (res.payload or {}).get("expected_ack")
            return code.hex() if isinstance(code, (bytes, bytearray)) else (code or True)

        async def give_up():
            contact = self.find_contact_by_key(pubkey_prefix)
            if contact and contact.get("out_path_len", -1) >= 0 and self._mc:
                logger.info("DM to %s never acked on its %d-hop path: resetting to flood",
                            pubkey_prefix[:8], contact["out_path_len"])
                await self._mc.commands.reset_path(contact.get("public_key") or pubkey_prefix)

        delivery_tracker.track(Outbound(kind="dm", hash=None, ack=str(ack), resend=resend, ev=ev,
                                        window_s=window, give_up=give_up))

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

        # The text and data roles may share one slot (v5: #meshwx carries
        # both), so decide the role once — the text handler must not run
        # twice for the same message.
        if channel_idx == self._channel_idx:
            on_text_channel = True
        elif channel_idx == self._data_channel_idx:
            on_text_channel = False        # data-only slot (a legacy deployment)
        else:
            return

        sender = "unknown"
        if ": " in text:
            sender, text = text.split(": ", 1)
        sender = clean_text(sender, 40) or "unknown"
        text = clean_text(text, 200)

        # A data-only channel carries no conversation; only the legacy app
        # request prefixes still reach the text handler.
        if not on_text_channel and not text.upper().startswith(("WXQ", "MWX")):
            return

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
        """A raw packet the node heard: feed the delivery tracker (echoes of
        our own sends) and, when enabled, MQTT. Never raises."""
        payload = event.payload or {}
        try:
            raw = payload.get("payload")
            if raw:
                delivery_tracker.on_rx_log(bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw),
                                           payload.get("snr"))
        except Exception:
            logger.debug("Unparseable RX log frame", exc_info=True)
        if self._mqtt is None:
            return
        try:
            self._mqtt.publish_packet(payload)
        except Exception:
            logger.exception("MQTT publish failed (non-fatal)")

    async def _on_ack(self, event) -> None:
        code = (event.payload or {}).get("code")
        if code:
            delivery_tracker.on_ack(code)

    # -- Losing the node ------------------------------------------------------
    #
    # Three ways to notice: the serial layer says the port closed, the
    # device node disappears from /dev, or the node stops answering
    # commands. Each ends in _link_lost(), once, which tells the bot to
    # reconnect (and adopt whatever answers next).

    LINK_CHECK_S = 15
    SILENT_COMMANDS = 3

    async def _on_disconnected(self, event) -> None:
        await self._link_lost("serial port closed: " + str((event.payload or {}).get("reason") or "unknown"))

    async def _link_watchdog(self) -> None:
        silent = 0
        while self._running:
            await asyncio.sleep(self.LINK_CHECK_S)
            if not self._running or not self._mc:
                return
            if self._port_real and not os.path.exists(self._port_real):
                await self._link_lost(f"{self._port_real} is gone (unplugged?)")
                return
            if getattr(self._mc, "is_connected", True) is False:
                await self._link_lost("serial layer reports no connection")
                return
            try:
                res = await self._mc.commands.get_bat()
                ok = res is not None and res.type != EventType.ERROR
            except Exception:
                ok = False
            silent = 0 if ok else silent + 1
            if silent >= self.SILENT_COMMANDS:
                await self._link_lost(f"node answered nothing to {silent} commands in a row")
                return

    async def _link_lost(self, reason: str) -> None:
        if self._lost or not self._running:
            return
        self._lost = True
        self._running = False
        logger.error("Radio link lost: %s", reason)
        if self._disconnect_handler:
            try:
                await self._disconnect_handler(reason)
            except Exception:
                logger.exception("Error in disconnect handler")

    async def _channel_secret(self, idx: int) -> bytes | None:
        """The slot's secret, cached; needed to know the bytes of what we send."""
        cached = self._channel_secrets.get(idx)
        if cached:
            return cached
        try:
            ch = await self._mc.commands.get_channel(idx)
            secret = ch.payload.get("channel_secret", b"") if ch.type == EventType.CHANNEL_INFO else b""
            if isinstance(secret, str):
                secret = bytes.fromhex(secret)
            if len(secret) == 16 and any(secret):
                self._channel_secrets[idx] = bytes(secret)
                return self._channel_secrets[idx]
        except Exception:
            logger.debug("Could not read channel %d secret", idx, exc_info=True)
        return None

    async def _on_new_contact(self, event) -> None:
        """PUSH_CODE_NEW_ADVERT: the node discovered a contact it did not have.
        In manual-add mode this arrives for every discovered node, stored or
        not; only companions (the people who can DM us) reach the bot."""
        c = event.payload or {}
        key = str(c.get("public_key", ""))
        if self._mc is not None:
            self._mc._pending_contacts.pop(key, None)
        name = clean_text(c.get("adv_name", "unknown"), 40) or "unknown"
        ctype = c.get("type")
        if ctype not in (None, 1):
            logger.debug("Advert from %s %s: not stored", CONTACT_TYPE_NAMES.get(ctype, ctype), name)
            return
        try:
            await self._mc.ensure_contacts(follow=True)
        except Exception:
            pass
        logger.info("New advert from %s (%s)", name, key[:12])
        await self._notify_advert(name, key[:12].lower())

    async def _on_advert(self, event) -> None:
        """PUSH_CODE_ADVERT: a contact the node already has adverted again."""
        key = str((event.payload or {}).get("public_key", ""))
        try:
            await self._mc.ensure_contacts(follow=True)
        except Exception:
            pass
        contact = (self._mc.contacts or {}).get(key) if self._mc else None
        name = clean_text((contact or {}).get("adv_name", "unknown"), 40) or "unknown"
        if contact is not None and contact.get("type") not in (None, 1):
            return
        logger.debug("Advert from %s (%s)", name, key[:12])
        await self._notify_advert(name, key[:12].lower())

    async def _notify_advert(self, name: str, prefix: str) -> None:
        if not self._advert_handler:
            return
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
        if self.pending_adoption:
            logger.info("Advert held: this radio is not the node in the profile yet")
            return
        try:
            await self._mc.commands.send_advert(flood=True)
            self.last_advert_at = time.time()
            logger.info("Sent advertisement (flood)")
            from meshcore_weather.traffic import traffic_log
            traffic_log.record("advert_out", sender=(self._mc.self_info or {}).get("name"))
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
            await self.housekeep_contacts()

    # -- Contact table housekeeping ---------------------------------------------
    #
    # The node stores at most MCW_CONTACT_SLOTS contacts and a person we have
    # not stored cannot be DMed. Repeaters, rooms and sensors take slots and
    # give the bot nothing (a DM path is repeater hashes, not contacts), so
    # they are removed; when people alone approach the limit, the ones heard
    # longest ago go. Never the admin, never a peer weather bot.

    last_housekeeping: dict = {"t": None, "removed": 0, "kept": 0, "people": 0, "note": ""}

    async def housekeep_contacts(self) -> dict:
        if not settings.contact_housekeeping or not self._mc:
            return self.last_housekeeping
        contacts = dict(self._mc.contacts or {})
        plan = plan_contact_removals(contacts, self.max_contacts, settings.contact_keep_free,
                                     settings.admin_key, settings.peer_bot_prefix,
                                     (self._mc.self_info or {}).get("public_key", ""))
        removed = 0
        for key, name, reason in plan:
            try:
                res = await self._mc.commands.remove_contact(key)
                if res.type == EventType.ERROR:
                    logger.warning("Could not remove contact %s: %s", name, res.payload)
                    continue
                removed += 1
                logger.info("Contacts: removed %s (%s)", name, reason)
            except Exception as e:
                logger.warning("Could not remove contact %s: %s", name, e)
        if removed:
            # The library's cache only merges what the node sends since the
            # last fetch; a removed contact never leaves it. Reload from zero
            # so what we report is what the node actually holds.
            try:
                self._mc._contacts.clear()
                self._mc._lastmod = 0
                await self._mc.commands.get_contacts(lastmod=0)
            except Exception:
                logger.debug("Contacts reload after housekeeping failed")
        left = dict(self._mc.contacts or {})
        people = sum(1 for c in left.values() if c.get("type") == 1)
        self.last_housekeeping = {"t": time.time(), "removed": removed, "kept": len(left), "people": people,
                                  "note": f"removed {removed}, {len(left)} left ({people} people) of {self.max_contacts} slots"}
        if removed:
            logger.info("Contacts housekeeping: %s", self.last_housekeeping["note"])
        return self.last_housekeeping

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
            "channels": {"text": self._channel_idx, "data": self._data_channel_idx},
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
                          "role": self._role_for(i), "roles": self._roles_for(i)})
        return chans

    def _roles_for(self, idx: int) -> list[str]:
        """Every role on a slot: one slot can carry both text and data."""
        return [r for r, (attr, _) in self._ROLES.items() if getattr(self, attr) == idx]

    def _role_for(self, idx: int) -> str | None:
        """The bot's role for a slot. Text wins when one slot carries both."""
        if idx == self._channel_idx:
            return "text"
        if idx == self._data_channel_idx:
            return "data"
        return None

    # Role -> (index attribute, settings attribute). The bot listens/sends on
    # these; the names in settings are what the operator configured.
    _ROLES = {
        "text": ("_channel_idx", "meshcore_channel"),
        "data": ("_data_channel_idx", "meshwx_channel"),
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
        getattr(self, "_channel_secrets", {}).pop(idx, None)
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
        create it on a free slot. Empty name detaches the role (data only).
        Text and data may name the same channel, in which case data simply
        points at the text slot. Returns the slot index."""
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
        # v5: one channel carries text and data. Nothing to create or rename.
        if role == "data" and self._channel_idx is not None and name == settings.meshcore_channel:
            self._data_channel_idx = self._channel_idx
            settings.meshwx_channel = name
            logger.info("Channel role data -> slot %d (%s, shared with text)", self._channel_idx, name)
            return self._channel_idx
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
                # last_advert is the timestamp the peer wrote into its advert
                # (its own clock, often wrong); lastmod is when our node
                # stored it (our clock, set by the bot). Show the latter.
                "heard": c.get("lastmod"),
                "last_advert": c.get("last_advert"),
                "lat": c.get("adv_lat"), "lon": c.get("adv_lon"),
                "out_path_len": c.get("out_path_len"),
            })
        out.sort(key=lambda c: c.get("heard") or 0, reverse=True)
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

