"""Main entry point - wires EMWIN data, parser, and Meshcore radio together."""

import asyncio
import json
import logging
import re
import signal
import sys
import time
from pathlib import Path

from meshcore_weather.config import settings
from meshcore_weather.emwin.fetcher import create_source
from meshcore_weather.geodata import resolver
from meshcore_weather.meshcore.radio import MeshcoreRadio
from meshcore_weather.nlp import parse_intent
from meshcore_weather.core.pages import split_pages
from meshcore_weather.core.render_text import MAX_DM
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.traffic import traffic_log

logger = logging.getLogger(__name__)

RADIO_RETRY_SECONDS = 60

# One message, under the channel budget, no newlines (phones wrap it).
HELP_TEXT = (
    "Weather bot: wx/forecast/warn <city ST> | warn/storm/rain <ST> | "
    "metar <ICAO> | space | more. DM me for private replies"
)


STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "puerto rico": "PR", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "guam": "GU", "virgin islands": "VI",
}

VALID_STATES = set(STATE_NAMES.values())


def channel_fit(text: str, budget: int) -> str:
    """One channel message: the whole reply if it fits, else the reply cut at
    a list boundary with a note. Never cut mid-word, never overflow."""
    text = text.replace("\n", " ").strip()
    if len(text) <= budget:
        return text
    note = " … DM me for all"
    room = budget - len(note)
    cut = text[:room]
    for sep in ("; ", " | ", ", ", " "):
        i = cut.rfind(sep)
        if i > room * 3 // 5:
            cut = cut[:i]
            break
    return cut.rstrip(" ;|,") + note


class WeatherBot:
    """Main application: bridges EMWIN weather data to Meshcore radio."""

    _CONTACTS_FILE = Path(settings.data_dir) / "known_contacts.json"

    def __init__(self):
        self.emwin = create_source()
        self.radio = MeshcoreRadio()
        self.store = WeatherStore()
        self._running = False
        self._refresh_task: asyncio.Task | None = None
        self._radio_task: asyncio.Task | None = None
        self._radio_last_error: str | None = None
        self._started_at: float = time.time()
        self._broadcaster = None  # MeshWXBroadcaster, created if data channel configured
        self._portal = None  # PortalServer, created if portal enabled
        self._paging: dict[str, dict] = {}  # sender_key -> {full, offset, ts}
        self._rate_limit: dict[str, float] = {}
        self._reply_history: dict[str, list[float]] = {}
        self._all_replies: list[float] = []
        self._channel_reply_by_sender: dict[str, float] = {}
        self._channel_replies: list[float] = []
        self._sdr_monitor = None
        # Map sender names to pubkey prefixes — persisted to disk
        self._known_contacts: dict[str, str] = {}  # name -> pubkey_prefix
        # Names where DM has failed — don't try again until they re-advert
        self._dm_blocked: set[str] = set()
        # Track channel usage for unknown contacts
        # Track consecutive channel msgs from known contacts (DM may not be working)
        self._load_known_contacts()

    async def start(self) -> None:
        logger.info("Starting Meshcore Weather Bot")
        logger.info("  Serial port: %s", settings.serial_port)
        logger.info("  EMWIN source: %s", settings.emwin_source)
        logger.info("  Channel: %s", settings.meshcore_channel)

        from meshcore_weather.portal import logbuf
        logbuf.install(asyncio.get_running_loop())   # console buffer catches everything from here on
        traffic_log.install(asyncio.get_running_loop())
        resolver.load()   # also sets the resolver home from MCW_HOME_CITIES
        if settings.emwin_source == "sdr":
            from meshcore_weather.sdr_monitor import SdrMonitor
            self._sdr_monitor = SdrMonitor()
            self._sdr_monitor.start()
        self.radio.on_channel_message(self._handle_channel_message)
        self.radio.on_dm(self._handle_dm)
        self.radio.on_advert(self._handle_advert)
        self.radio.on_disconnect(self._handle_radio_lost)

        await self.emwin.start()
        await self._refresh_store()

        self._running = True
        self._refresh_task = asyncio.create_task(self._refresh_loop())

        # The radio may not be plugged in yet (a Pi whose Heltec arrives
        # later, a USB cable pulled). Keep serving the store, portal and
        # CLI, and keep trying the radio until it answers.
        try:
            await self.radio.start()
        except Exception as e:
            self._radio_last_error = str(e)
            logger.warning("Radio not available (%s); retrying every %ds", e, RADIO_RETRY_SECONDS)
            self._radio_task = asyncio.create_task(self._radio_retry_loop())
        else:
            await self._after_radio_connected()

        # Start local operator web portal if enabled
        if settings.portal_enabled:
            try:
                from meshcore_weather.portal.server import PortalServer
                self._portal = PortalServer(self)
                await self._portal.start()
            except ImportError as e:
                logger.warning("Portal disabled: %s (run `pip install meshcore-weather[portal]`)", e)

        if self.radio.channel_idx is not None:
            logger.info(
                "Weather bot is running. Listening on channel %d (%s) + DMs",
                self.radio.channel_idx,
                settings.meshcore_channel,
            )
        else:
            logger.info("Weather bot is running without a radio (store, portal and CLI only)")

    async def _after_radio_connected(self) -> None:
        """Start the data-channel broadcaster once a radio with a data channel is up."""
        if self.radio.data_channel_idx is not None:
            from meshcore_weather.protocol.broadcaster import AppResponder
            self._broadcaster = AppResponder(self.store, self.radio, render_text=self._process_command)
            await self._broadcaster.start()

    async def _handle_radio_lost(self, reason: str) -> None:
        """The radio reports its link is gone: reconnect in the background
        (a swapped board is adopted on the way) and keep serving meanwhile."""
        self._radio_last_error = f"link lost: {reason}"
        logger.warning("Reconnecting to the radio after a lost link (%s)", reason)
        self._radio_task = asyncio.create_task(self.reconnect_radio())

    async def reconnect_radio(self) -> None:
        """Drop the radio link and connect again (serial port changed, node
        rebooted). Falls back to the retry loop if it does not come up."""
        if self._radio_task and self._radio_task is not asyncio.current_task():
            self._radio_task.cancel()
        self._radio_task = None
        try:
            await self.radio.stop()
        except Exception:
            pass
        if self._broadcaster:
            try:
                await self._broadcaster.stop()
            except Exception:
                pass
            self._broadcaster = None
        self.radio = MeshcoreRadio()
        self.radio.on_channel_message(self._handle_channel_message)
        self.radio.on_dm(self._handle_dm)
        self.radio.on_advert(self._handle_advert)
        self.radio.on_disconnect(self._handle_radio_lost)
        try:
            await self.radio.start()
        except Exception as e:
            self._radio_last_error = str(e)
            logger.warning("Radio not available after reconnect (%s); retrying every %ds", e, RADIO_RETRY_SECONDS)
            self._radio_task = asyncio.create_task(self._radio_retry_loop())
            return
        self._radio_last_error = None
        await self._after_radio_connected()

    def request_restart(self, delay: float = 0.5) -> None:
        """Exit cleanly a moment from now; systemd (Restart=always) brings the
        bot back with the current .env."""
        loop = asyncio.get_running_loop()

        async def _go():
            await asyncio.sleep(delay)
            logger.info("Restart requested from the portal; exiting")
            try:
                await self.stop()
            finally:
                loop.stop()

        loop.create_task(_go())

    async def _radio_retry_loop(self) -> None:
        while self._running:
            await asyncio.sleep(RADIO_RETRY_SECONDS)
            try:
                await self.radio.start()
            except Exception as e:
                self._radio_last_error = str(e)
                logger.debug("Radio still not available: %s", e)
                continue
            self._radio_last_error = None
            logger.info("Radio connected after retry. Listening on channel %s (%s) + DMs",
                        self.radio.channel_idx, settings.meshcore_channel)
            await self._after_radio_connected()
            return

    async def stop(self) -> None:
        logger.info("Shutting down Weather Bot")
        self._running = False
        if self._radio_task:
            self._radio_task.cancel()
            try:
                await self._radio_task
            except asyncio.CancelledError:
                pass
        if self._portal:
            await self._portal.stop()
        if self._sdr_monitor:
            await self._sdr_monitor.stop()
        if self._broadcaster:
            await self._broadcaster.stop()
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        await self.radio.stop()
        await self.emwin.stop()
        traffic_log.flush(force=True)
        logger.info("Weather bot stopped")

    async def _refresh_loop(self) -> None:
        while self._running:
            await asyncio.sleep(settings.emwin_poll_interval)
            await self._refresh_store()

    async def _refresh_store(self) -> None:
        products = await self.emwin.fetch_products()
        if products:
            self.store.ingest(products)
            await self._warm_warnings()

    async def _warm_warnings(self) -> None:
        """Parse new warning products now, off the event loop, so the first
        DM after a refresh does not wait on pyIEM (15-25 s on a Pi 4)."""
        from functools import partial
        from meshcore_weather.protocol.warnings import extract_active_warnings
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, partial(extract_active_warnings, self.store, coverage=None))
        except Exception:
            logger.debug("warning cache warm-up failed", exc_info=True)

    # -- Message handling --

    async def _handle_channel_message(self, channel: str, sender: str, text: str,
                                      hops: int | None = None) -> None:
        """Handle a message received on a channel. `hops` is how many
        repeaters the packet crossed (None when the frame did not say)."""
        ch = int(channel)
        if ch == 0:
            return
        text = text.strip()
        # Bots ignore bots: another WX-* node's channel reply is not a request.
        if sender.upper().startswith(settings.peer_bot_prefix.upper()):
            if text:
                traffic_log.record("peer", sender=sender, text=text, hops=hops)
            return

        if not text:
            return

        # An app request (">w", ">f 102"): answered on the data channel.
        if text.startswith(">"):
            await self._handle_app_request(text, sender, self.person_key(sender), "channel", hops)
            return

        # Text commands only on the text channel — don't parse data channel noise
        if ch != self.radio.channel_idx:
            return

        req = traffic_log.record("channel_in", sender=sender, text=text, hops=hops)
        command, location = await self._parse(text)
        traffic_log.update(req, command=command, location=location)
        if not self._rate_check(sender, follow_up=(command == "more")):
            traffic_log.record("dropped", reason="rate limit", req=req, text=text)
            return

        # Replies go by DM when we can: a DM with a known path costs only
        # the repeaters on it, a channel reply floods every repeater. For a
        # sender we cannot DM (their phone has never heard our advert, or
        # ours has never heard theirs) we answer once on OUR channel, never
        # on the public one, rate-limited, and advert so the next exchange
        # can be a DM.
        if not self._we_answer(command, location):
            traffic_log.record("dropped", reason="a nearer bot answers", req=req)
            return
        mode = settings.reply_mode
        if mode == "channel":
            await self._respond_channel(sender, command, location, hops, forced=True, req=req)
            return
        pubkey = self._resolve_sender_key(sender)
        if not pubkey:
            if mode == "dm_only":
                logger.info("Channel command from %s: no DM path and reply_mode=dm_only — ignoring", sender)
                traffic_log.record("dropped", reason="no DM path (dm_only)", req=req)
                return
            await self._respond_channel(sender, command, location, hops, req=req)
            return
        await self._respond_dm(pubkey, sender, command, location, req=req)

    # Commands that name a place are answered by the nearest bot only.
    _PLACE_COMMANDS = {"wx", "forecast", "warn", "outlook", "metar", "taf", "nowcast"}

    def _we_answer(self, command: str, location: str) -> bool:
        """Overlap rule: for a request that names a place, answer only if we
        are the nearest weather bot to that place among the bots we have
        heard adverts from (ties: lower public key). Requests without a
        place are answered by every bot that hears them (by DM, cheap)."""
        if command not in self._PLACE_COMMANDS or not location:
            return True
        peers = self.radio.peer_bots() if hasattr(self.radio, "peer_bots") else []
        if not peers:
            return True
        home = resolver.home()
        if home is None:
            return True
        loc = resolver.resolve(location)
        if not loc or loc.get("lat") is None:
            return True                      # unresolvable: let the reply say so
        from meshcore_weather.geodata import _haversine
        mine = _haversine(home[0], home[1], loc["lat"], loc["lon"])
        my_key = (getattr(self.radio, "public_key", "") or "").lower()
        for p in peers:
            d = _haversine(p["lat"], p["lon"], loc["lat"], loc["lon"])
            if d < mine or (d == mine and p["public_key"].lower() < my_key):
                logger.info("Not answering %s %r: %s is nearer (%.0f km vs our %.0f km)",
                            command, location, p["name"], d, mine)
                return False
        return True

    # Channel-reply budget for senders we cannot DM: per sender and overall.
    CHANNEL_REPLY_PER_SENDER_S = 600
    CHANNEL_REPLY_PER_HOUR = 12

    async def _respond_channel(self, sender: str, command: str, location: str,
                               hops: int | None = None, forced: bool = False,
                               req: dict | None = None) -> None:
        """Reply on our own channel (a flood). `forced` is reply_mode=channel;
        otherwise this is the stranger fallback with its hop gate and budget."""
        now = time.time()
        if not forced:
            if hops is not None and hops > settings.channel_reply_max_hops:
                logger.info("Channel command from %s: no DM path and %d hops away — too far for a channel reply",
                            sender, hops)
                traffic_log.record("dropped", reason=f"no DM path, {hops} hops away", req=req, sender=sender)
                return
            self._channel_replies = [ts for ts in getattr(self, "_channel_replies", []) if now - ts < 3600]
            last = self._channel_reply_by_sender.get(sender, 0.0)
            if now - last < self.CHANNEL_REPLY_PER_SENDER_S:
                logger.info("Channel command from %s: no DM path, channel reply already sent %ds ago — ignoring",
                            sender, int(now - last))
                traffic_log.record("dropped", reason=f"no DM path, channel reply {int(now - last)}s ago",
                                   req=req, sender=sender)
                return
            if len(self._channel_replies) >= self.CHANNEL_REPLY_PER_HOUR:
                logger.warning("Channel command from %s: no DM path and the hourly channel-reply budget is spent — ignoring", sender)
                traffic_log.record("dropped", reason="hourly channel-reply budget spent", req=req, sender=sender)
                return
        if forced:
            # Channel mode: the same paged reply a DM would get, so "more" works.
            chunk, _ = self.reply_chunk(command, location, self.person_key(sender))
        else:
            # A stranger gets one message and no paging session: their "more"
            # would only hit the per-sender channel gate. DM is the way to the rest.
            response = self._process_command(command, location)
            chunk = channel_fit(response, self.radio.channel_text_budget()) if response else None
        if not chunk:
            traffic_log.record("dropped", reason="nothing to say", req=req, sender=sender)
            return
        if not forced:
            self._channel_reply_by_sender[sender] = now
            self._channel_replies.append(now)
            logger.info("Channel command from %s: no DM path, replying on our channel (flood)", sender)
        else:
            logger.info("Channel command from %s: reply_mode=channel, replying on our channel (flood)", sender)
        ev = traffic_log.record("reply_channel", text=chunk, chars=len(chunk), req=req, sender=sender,
                                command=command, location=location, ok=settings.tx_enabled)
        await self.radio.send_channel_message(self.radio.channel_idx, chunk[:160], ev=ev)
        if not forced and await self.radio.advert_if_stale():
            logger.info("Adverted so %s can DM us next time", sender)

    async def _handle_dm(self, pubkey_prefix: str, sender_name: str, text: str) -> None:
        """Handle a direct message."""
        text = text.strip()
        if not text:
            return

        prefix = self._normalize_key(pubkey_prefix)
        # Binary data requests (WXQ/MWX prefixes) bypass the 5-second per-user
        # rate check — they have their own per-(data_type, location) rate limit
        # at the broadcaster level (5 min), and iOS clients legitimately fire
        # multiple binary requests back-to-back when fetching different data
        # types for the same location. The 5-second check is meant for human
        # users typing text commands like "wx austin" / "forecast", not for
        # apps doing structured queries.
        is_binary_request = text.startswith("WXQ") or text.startswith("MWX")
        req = None
        command = location = ""
        if is_binary_request:
            traffic_log.record("data_request", sender=sender_name, key=prefix, text=text[:24], transport="dm")
        else:
            req = traffic_log.record("dm_in", sender=sender_name, key=prefix, text=text)

        # Parse @lat,lng prefix for location-aware commands
        loc_match = re.match(r"^@(-?\d+\.?\d*),(-?\d+\.?\d*)\s+(.*)", text)
        if loc_match:
            try:
                lat = float(loc_match.group(1))
                lon = float(loc_match.group(2))
            except ValueError:
                lat = lon = None
            text = loc_match.group(3)
            if lat is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                if not hasattr(self, "_user_locations"):
                    self._user_locations = {}
                if len(self._user_locations) > 2000:
                    self._user_locations.clear()
                self._user_locations[prefix] = (lat, lon)
                logger.info("Cached location for %s: %.4f, %.4f", sender_name, lat, lon)

        if not is_binary_request:
            command, location = await self._parse(text)
            traffic_log.update(req, command=command, location=location)
            if not self._rate_check(prefix, follow_up=(command == "more")):
                logger.debug("DM rate-limited from %s", sender_name)
                traffic_log.record("dropped", reason="rate limit", req=req)
                return

        # They're DMing us — DMs work both ways, clear all blocks
        if sender_name and sender_name != "unknown":
            is_new = sender_name not in self._known_contacts
            self._known_contacts[sender_name] = prefix
            self._dm_blocked.discard(sender_name)
            if is_new:
                self._save_known_contacts()
            # A paging session opened by their channel request continues by DM.
            ch_key = "ch:" + sender_name
            if ch_key in self._paging and prefix not in self._paging:
                self._paging[prefix] = self._paging.pop(ch_key)

        if text.startswith(">"):
            traffic_log.update(req, kind="data_request")
            await self._handle_app_request(text, sender_name, prefix, "dm", None, req=req)
            return

        # Admin commands (DM-only, verified by pubkey)
        if self._is_admin(prefix):
            result = await self._handle_admin(text, prefix, sender_name)
            if result is not None:
                traffic_log.update(req, kind="admin", command=text.split(None, 1)[0].lower())
                return

        await self._respond_dm(prefix, sender_name, command, location, req=req)

    async def _handle_advert(self, contact_name: str, pubkey_prefix: str) -> None:
        """Handle a new advert — only greet users who were using the channel."""
        prefix = self._normalize_key(pubkey_prefix)
        traffic_log.record("advert", sender=contact_name, key=prefix)

        # If they already DM us fine, just update the mapping — no greeting
        if contact_name in self._known_contacts:
            return

        # Remember this new contact (bounded: names are attacker-chosen)
        if contact_name and contact_name != "unknown":
            if len(self._known_contacts) > 2000:
                self._known_contacts = dict(list(self._known_contacts.items())[-1000:])
            self._known_contacts[contact_name] = prefix
            self._save_known_contacts()

        # Unblock DM if they were blocked. No re-advert and no welcome DM:
        # both are unsolicited airtime. They will DM us when they want data.
        self._dm_blocked.discard(contact_name)

    async def _handle_app_request(self, text: str, sender_name: str, sender_key: str, transport: str,
                                  hops: int | None, req: dict | None = None) -> None:
        """A `>` request from an app (docs/MeshWX_v5_Spec.md 8.2)."""
        if req is None:
            req = traffic_log.record("data_request", sender=sender_name, key=sender_key if transport == "dm" else None,
                                     text=text[:40], transport=transport, hops=hops)
        if not self._broadcaster:
            traffic_log.record("dropped", reason="broadcasts off: no data channel", req=req, sender=sender_name)
            return
        outcome = await self._broadcaster.handle_request(text, sender_key)
        logger.info("App request from %s: %s -> %s", sender_name, text[:40], outcome)
        if outcome in ("rate limited", "hourly budget spent"):
            traffic_log.record("dropped", reason=outcome, req=req, sender=sender_name)

    def _is_admin(self, pubkey_prefix: str) -> bool:
        admin = settings.admin_key.lower().strip()
        return bool(admin) and pubkey_prefix.startswith(admin)

    async def _handle_admin(self, text: str, prefix: str, sender_name: str) -> str | None:
        """Handle admin commands. Returns response string, or None if not an admin command."""
        parts = text.strip().split(None, 1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "contacts":
            await self.radio._mc.ensure_contacts(follow=True)
            contacts = self.radio._mc._contacts or {}
            if not contacts:
                reply = "No contacts on device."
            else:
                lines = [f"{len(contacts)} contacts:"]
                for c in contacts.values():
                    name = c.get("adv_name", "?")
                    key = c.get("public_key", "")[:12]
                    lines.append(f" {name} ({key})")
                reply = "\n".join(lines)
            await self._send_dm_paginated(prefix, sender_name, reply)
            return reply

        if cmd == "remove" and arg:
            await self.radio._mc.ensure_contacts(follow=True)
            contact = self.radio._mc.get_contact_by_name(arg)
            if not contact:
                await self.radio.send_dm(prefix, f"Contact '{arg}' not found.")
                return "not found"
            key = contact.get("public_key", "")
            name = contact.get("adv_name", "?")
            # Confirm before removing (can't DM after delete)
            is_self = self._normalize_key(key) == prefix
            if is_self:
                await self.radio.send_dm(prefix, f"Removing: {name} (you). Re-advert to reconnect.")
            else:
                await self.radio.send_dm(prefix, f"Removing: {name}")
            try:
                await self.radio._mc.commands.remove_contact(key)
                self._known_contacts.pop(name, None)
                logger.info("Admin %s removed contact: %s", sender_name, name)
            except Exception as e:
                logger.warning("Failed to remove %s: %s", name, e)
            return "removed"

        if cmd == "clear-contacts":
            await self.radio._mc.ensure_contacts(follow=True)
            contacts = self.radio._mc._contacts or {}
            if not contacts:
                await self.radio.send_dm(prefix, "No contacts to remove.")
                return "empty"
            contact_list = list(contacts.values())
            await self.radio.send_dm(prefix, f"Clearing {len(contact_list)} contacts. Re-advert to reconnect.")
            removed = 0
            for c in contact_list:
                try:
                    await self.radio._mc.commands.remove_contact(c["public_key"])
                    self._known_contacts.pop(c.get("adv_name", ""), None)
                    removed += 1
                except Exception:
                    pass
            logger.info("Admin %s cleared %d/%d contacts", sender_name, removed, len(contact_list))
            return "cleared"

        if cmd == "advert":
            await self.radio._send_advert()
            await self.radio._mc.ensure_contacts(follow=True)
            await self.radio.send_dm(prefix, "Advert sent + contacts refreshed.")
            return "advert"

        if cmd == "refresh":
            await self.radio._mc.ensure_contacts(follow=True)
            count = len(self.radio._mc._contacts or [])
            await self.radio.send_dm(prefix, f"Contacts refreshed: {count} contacts.")
            return "refresh"

        if cmd == "broadcast":
            if not self._broadcaster:
                await self.radio.send_dm(prefix, "Broadcasts are off: no data channel configured.")
                return "disabled"
            await self.radio.send_dm(prefix, "Running scheduler tick...")
            try:
                sent = await self._broadcaster.scheduler.tick()
                await self.radio.send_dm(prefix, f"Scheduler tick sent {sent} message(s).")
            except Exception as e:
                await self.radio.send_dm(prefix, f"Broadcast error: {e}")
            return "broadcast"

        if cmd == "warnings-broadcast":
            if not self._broadcaster:
                await self.radio.send_dm(prefix, "Broadcasts are off: no data channel configured.")
                return "disabled"
            try:
                sent = await self._broadcaster.scheduler.run_job_now("warnings")
                await self.radio.send_dm(prefix, f"Sent {sent} warning message(s).")
            except Exception as e:
                await self.radio.send_dm(prefix, f"Warning broadcast error: {e}")
            return "warnings-broadcast"

        if cmd == "test-data-ch":
            ch = self.radio.data_channel_idx
            if ch is None:
                await self.radio.send_dm(prefix, "No data channel configured.")
                return "no ch"
            if not settings.tx_enabled:
                await self.radio.send_dm(prefix, "TX disabled — test ping suppressed.")
                return "tx-disabled"
            await self.radio._mc.commands.send_chan_msg(ch, "test ping")
            await self.radio.send_dm(prefix, f"Sent text test on ch {ch}.")
            return "test"

        if cmd == "admin":
            reply = (
                "Admin commands (DM only):\n"
                "contacts - list contacts\n"
                "remove <name> - remove contact\n"
                "clear-contacts - remove all\n"
                "advert - send advert now\n"
                "refresh - reload contacts\n"
                "broadcast - run a scheduler tick now\n"
                "warnings-broadcast - send warnings"
            )
            await self._send_dm_paginated(prefix, sender_name, reply)
            return reply

        return None  # Not an admin command, fall through to normal handling

    async def _send_dm_paginated(self, pubkey: str, sender_name: str, text: str) -> None:
        """Send a long admin reply: page 1 now, the rest on 'more'."""
        pages = self._start_session(self._normalize_key(pubkey), "admin", text)
        await self.radio.send_dm(pubkey, pages[0])

    # -- Replies and paging --
    #
    # Every reply is rendered in full and cut into numbered pages at the
    # message budget (core/pages.py). Page 1 answers the request; "more"
    # from the same person sends the next page, on whichever transport the
    # "more" arrived. A person is their public key when we know it, else
    # their channel name; a session opened on the channel moves to the key
    # the first time they DM us.

    PAGE_SESSION_TTL_S = 900
    PAGE_SESSIONS_MAX = 500

    def page_budget(self) -> int:
        """Pages fit both transports: the DM budget (155) and the channel
        budget (153 minus our name), never more than MAX_DM."""
        try:
            return min(MAX_DM, int(self.radio.channel_text_budget()))
        except Exception:
            return MAX_DM

    def person_key(self, sender_name: str, pubkey: str | None = None) -> str:
        if pubkey:
            return self._normalize_key(pubkey)
        known = self._known_contacts.get(sender_name)
        if known:
            return self._normalize_key(known)
        contact = self.radio.find_contact_by_name(sender_name) if hasattr(self.radio, "find_contact_by_name") else None
        if contact and contact.get("public_key"):
            return self._normalize_key(contact["public_key"])
        return "ch:" + sender_name

    def _prune_sessions(self, now: float) -> None:
        cutoff = now - self.PAGE_SESSION_TTL_S
        if any(v["ts"] <= cutoff for v in self._paging.values()) or len(self._paging) > self.PAGE_SESSIONS_MAX:
            live = sorted(((v["ts"], k) for k, v in self._paging.items() if v["ts"] > cutoff), reverse=True)
            self._paging = {k: self._paging[k] for _, k in live[: self.PAGE_SESSIONS_MAX]}

    def _start_session(self, key: str, command: str, response: str) -> list[str]:
        now = time.time()
        self._prune_sessions(now)
        pages = split_pages(response, self.page_budget())
        self._paging[key] = {"pages": pages, "next": 1, "ts": now, "command": command}
        return pages

    def reply_chunk(self, command: str, location: str, sender_key: str) -> tuple[str | None, bool]:
        """The one message this person gets now, and whether a 'more' would
        fetch another. Shared by the DM path, channel mode, the CLI and the
        portal console, so they all page the same way."""
        now = time.time()
        self._prune_sessions(now)
        if command == "more":
            session = self._paging.get(sender_key)
            if not session:
                return "Nothing to continue. Send a command first.", False
            if session["next"] >= len(session["pages"]):
                return f"That was the whole reply to '{session['command']}'. Send a new command.", False
            page = session["pages"][session["next"]]
            session["next"] += 1
            session["ts"] = now
            return page, session["next"] < len(session["pages"])
        response = self._process_command(command, location)
        if not response:
            return None, False
        pages = self._start_session(sender_key, (command + " " + location).strip(), response)
        return pages[0], len(pages) > 1

    async def _respond_dm(self, pubkey_prefix: str, sender_name: str, command: str, location: str,
                          req: dict | None = None) -> None:
        """Send the reply as a DM."""
        chunk, _ = self.reply_chunk(command, location, self.person_key(sender_name, pubkey_prefix))
        if not chunk:
            traffic_log.record("dropped", reason="nothing to say", req=req, sender=sender_name, key=pubkey_prefix)
            return

        ev = traffic_log.record("reply_dm", text=chunk, chars=len(chunk), req=req, sender=sender_name,
                                key=pubkey_prefix, command=command, location=location, ok=settings.tx_enabled)
        success = await self.radio.send_dm(pubkey_prefix, chunk, ev=ev)
        if not success:
            traffic_log.update(ev, kind="dm_failed", ok=False)
        if success:
            logger.info("Response to %s (DM): %s", sender_name, chunk.replace("\n", " | "))
        else:
            # DM failed: drop it. The user will retry; no channel fallback,
            # no advert. Forget the stale mapping so a fresh advert re-learns it.
            logger.info("DM to %s failed — forgetting the path; the next channel command gets a channel reply", sender_name)
            self._dm_blocked.add(sender_name)
            self._known_contacts.pop(sender_name, None)

    # -- Helpers --

    @staticmethod
    def _normalize_key(key: str) -> str:
        """Normalize a pubkey to 12-char prefix for consistent session keying."""
        return key[:12].lower()

    def _resolve_sender_key(self, sender_name: str) -> str | None:
        """Try to find a pubkey prefix for a channel message sender so we can DM them."""
        # Don't try DM for contacts where it has previously failed
        if sender_name in self._dm_blocked:
            return None
        # Check our learned contacts first
        if sender_name in self._known_contacts:
            return self._known_contacts[sender_name]
        # Try the device's contact list
        contact = self.radio.find_contact_by_name(sender_name)
        if contact:
            pubkey = contact.get("public_key", "")
            if pubkey:
                prefix = self._normalize_key(pubkey)
                self._known_contacts[sender_name] = prefix
                self._save_known_contacts()
                return prefix
        return None

    def _load_known_contacts(self) -> None:
        try:
            if self._CONTACTS_FILE.exists():
                self._known_contacts = json.loads(self._CONTACTS_FILE.read_text())
                logger.info("Loaded %d known contacts from disk", len(self._known_contacts))
        except Exception:
            logger.debug("Could not load known contacts")

    def _save_known_contacts(self) -> None:
        try:
            self._CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._CONTACTS_FILE.write_text(json.dumps(self._known_contacts))
        except Exception:
            logger.debug("Could not save known contacts")

    # Reply budgets: one every 5 s per sender, at most this many per sender
    # and in total per hour. Every reply costs airtime; a script hammering
    # the channel must not be able to spend the mesh's.
    REPLIES_PER_SENDER_PER_HOUR = 40
    REPLIES_PER_HOUR = 400

    def _rate_check(self, sender_key: str, follow_up: bool = False) -> bool:
        """One reply per sender per 5 s, plus hourly budgets. A 'more' is a
        follow-up to a reply we just sent and only needs 2 s of spacing;
        it still counts against the hourly budgets."""
        now = time.time()
        last = self._rate_limit.get(sender_key, 0)
        if now - last < (2 if follow_up else 5):
            return False
        hour_ago = now - 3600
        hist = [ts for ts in self._reply_history.get(sender_key, []) if ts > hour_ago]
        self._reply_history[sender_key] = hist
        if len(hist) >= self.REPLIES_PER_SENDER_PER_HOUR:
            logger.warning("Rate limit: %s has had %d replies this hour — ignoring", sender_key[:20], len(hist))
            return False
        self._all_replies = [ts for ts in self._all_replies if ts > hour_ago]
        if len(self._all_replies) >= self.REPLIES_PER_HOUR:
            logger.warning("Rate limit: %d replies this hour overall — ignoring", len(self._all_replies))
            return False
        self._rate_limit[sender_key] = now
        hist.append(now)
        self._all_replies.append(now)
        # Bounded state: a flood of made-up sender names must not grow memory.
        if len(self._rate_limit) > 5000:
            self._rate_limit = {k: v for k, v in self._rate_limit.items() if now - v < 3600}
            self._reply_history = {k: v for k, v in self._reply_history.items() if v and v[-1] > hour_ago}
            self._channel_reply_by_sender = {k: v for k, v in self._channel_reply_by_sender.items() if now - v < 3600}
        return True

    async def _parse(self, text: str) -> tuple[str, str]:
        text = text[:200]
        text = "".join(c for c in text if c.isprintable() or c in "\n ")
        intent = await parse_intent(text)
        command = intent["command"]
        location = intent["location"]
        # "warn more", "wx more": the user wants the next page, not Skidmore, TX.
        if location.strip().lower() in ("more", "next") and command != "more":
            command, location = "more", ""
        location = "".join(c for c in location if c.isalnum() or c in " ,.-'")[:50]
        return command, location

    @staticmethod
    def _to_state_code(text: str) -> str | None:
        t = text.strip()
        if len(t) == 2 and t.upper() in VALID_STATES:
            return t.upper()
        name = t.lower()
        if name in STATE_NAMES:
            return STATE_NAMES[name]
        return None

    # -- Place replies via the core service layer -----------------------------
    #
    # wx / warn / forecast for a place all go through core.services so the
    # text a human reads and the bytes an app decodes come from the same
    # parse of the same products.

    def _place_reply(self, kind: str, location: str) -> str:
        from meshcore_weather.core import render_text, services
        loc = resolver.resolve(location)
        if not loc:
            return f"Unknown location: {location}"
        if kind == "warn":
            return render_text.warnings(loc, services.warnings_for(self.store, loc))
        if kind == "forecast":
            return render_text.forecast(loc, services.forecast_for(self.store, loc))
        if kind == "outlook":
            return render_text.outlook(loc, services.outlook_for(self.store, loc))
        if kind == "metar":
            return render_text.raw_metar(loc, services.raw_metar_for(self.store, loc))
        if kind == "taf":
            return render_text.taf(loc, services.taf_for(self.store, loc))
        ob = services.observation_for(self.store, loc)
        ws = services.warnings_for(self.store, loc)
        fc = services.forecast_for(self.store, loc)
        return render_text.summary(loc, ob, ws, fc)

    def _area_reply(self, kind: str, location: str) -> str:
        """rain / storm: for a state code, or the state of a place, or the
        bot's home state when nothing is given."""
        from meshcore_weather.core import render_text, services
        state = self._to_state_code(location) if location else None
        label = ""
        if state is None:
            target = location or (settings.home_cities.split(",")[0].strip() if settings.home_cities else "")
            loc = resolver.resolve(target) if target else None
            if not loc or not loc.get("zones"):
                return f"Unknown location: {location}" if location else "Usage: rain <ST or city ST>"
            state = loc["zones"][0][:2]
            label = f"in {state}"
        else:
            label = f"in {state}"
        if kind == "rain":
            return render_text.rain(label, services.rain_for(self.store, state=state))
        return render_text.storm_reports(label, services.storm_reports_for(self.store, state=state), state=state)

    def _process_command(self, command: str, location: str) -> str | None:
        if command == "help":
            return HELP_TEXT

        from meshcore_weather.core import overview

        if command == "wx":
            if not location:
                return overview.national(self.store)
            state = self._to_state_code(location)
            if state:
                return overview.state(self.store, state)
            return self._place_reply("wx", location)

        if command == "warn":
            if not location:
                return overview.warnings_summary(self.store)
            state = self._to_state_code(location)
            if state:
                return overview.warnings_in_state(self.store, state)
            return self._place_reply("warn", location)

        if command == "forecast":
            if not location:
                return "Usage: forecast <city ST>"
            return self._place_reply("forecast", location)

        if command == "outlook":
            if not location:
                return "Usage: outlook <city ST>"
            return self._place_reply("outlook", location)

        if command == "rain":
            return self._area_reply("rain", location)

        if command == "storm":
            return self._area_reply("storm", location)

        if command == "metar":
            if not location:
                return "Usage: metar <ICAO or city ST>"
            return self._place_reply("metar", location)

        if command == "taf":
            if not location:
                return "Usage: taf <ICAO or city ST>"
            return self._place_reply("taf", location)

        if command == "space":
            from meshcore_weather.core import space_weather
            return space_weather.render(space_weather.space_weather_for(self.store))

        return None


def main():
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Library chatter that would drown the console: one HTTP line per poll.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    bot = WeatherBot()
    loop = asyncio.new_event_loop()

    def shutdown(sig):
        logger.info("Received signal %s, shutting down...", sig.name)

        async def _stop_then_exit():
            try:
                await bot.stop()
            finally:
                loop.stop()          # without this run_forever() never returns

        loop.create_task(_stop_then_exit())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(bot.start())
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if bot._running:
            loop.run_until_complete(bot.stop())
        loop.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
