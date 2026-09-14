"""MeshWX broadcaster — reactive path (responds to client DMs).

The proactive periodic broadcast cycle lives in
`meshcore_weather.schedule.Scheduler` as of the unified schedule
refactor. This class now owns a Scheduler instance for proactive
broadcasts and handles the reactive paths:

  - `respond_to_data_request()` — builds a 0x30/0x31/0x32/... response
    to a client's 0x02 data request DM, with per-(data_type, location)
    rate limiting.
  - `broadcast_region()` — legacy 0x01 refresh request handler.

Both reactive paths share the radio with the Scheduler and use the same
builder methods (`_build_observation` etc.) under the hood. The
Scheduler uses its own executor for proactive broadcasts; the duplicate
builder code will be consolidated in a future cleanup.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from meshcore_weather.activity import EventDir, activity_log
from meshcore_weather.config import settings
from meshcore_weather.geodata import resolver
from meshcore_weather.meshcore.radio import MeshcoreRadio
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol.coverage import Coverage
from meshcore_weather.protocol.encoders import (
    encode_forecast_from_pfm,
    encode_forecast_from_zfp,
    encode_hwo,
    encode_lsr_reports,
    encode_metar,
    encode_rain_cities,
    encode_rwr_city,
    encode_taf,
    now_utc_minutes,
)
from meshcore_weather.protocol.meshwx import (
    V4SequenceCounter,
    DATA_FORECAST,
    DATA_METAR,
    DATA_OUTLOOK,
    DATA_RAIN_OBS,
    DATA_STORM_REPORTS,
    DATA_TAF,
    DATA_WARNING_DETAIL,
    DATA_SPACE_WEATHER,
    DATA_WARNINGS_NEAR,
    DATA_WX,
    LOC_LATLON,
    LOC_PFM_POINT,
    LOC_PLACE,
    LOC_STATION,
    LOC_WFO,
    LOC_ZONE,
    REASON_BOT_ERROR,
    REASON_LOCATION_UNRESOLVABLE,
    REASON_NO_DATA,
    REASON_PRODUCT_UNSUPPORTED,
    SEV_ADVISORY,
    SEV_WARNING,
    SEV_WATCH,
    cobs_encode,
    pack_not_available,
    pack_warnings_near,
)
from meshcore_weather.protocol.radar import (
    build_radar_messages,
    extract_region_grid,
    fetch_radar_composite,
)
from meshcore_weather.protocol.warnings import extract_active_warnings, warnings_to_binary

logger = logging.getLogger(__name__)

# Delay between consecutive LoRa transmissions (seconds)
TX_SPACING = 2


class MeshWXBroadcaster:
    """Reactive broadcast path — responds to client data requests.

    The proactive periodic broadcast cycle lives in the Scheduler
    (`meshcore_weather.schedule.scheduler.Scheduler`), which this class
    owns as a member. `start()` launches the scheduler's tick loop.
    `respond_to_data_request()` handles incoming client DMs by building
    the right wire message and broadcasting it on the data channel —
    separate from the scheduled path.
    """

    def __init__(self, store: WeatherStore, radio: MeshcoreRadio):
        # Local import avoids a circular dependency between this module
        # and meshcore_weather.schedule.executor (which also imports from
        # meshcore_weather.protocol).
        from meshcore_weather.schedule.scheduler import Scheduler

        self.store = store
        self.radio = radio
        self._last_refresh: dict[int, float] = {}  # region_id -> timestamp
        self._scheduler: Scheduler = Scheduler(store=store, radio=radio)

    @property
    def scheduler(self):
        """The owned Scheduler instance (for portal access)."""
        return self._scheduler

    @property
    def coverage(self) -> Coverage:
        """Current coverage — delegates to the Scheduler."""
        return self._scheduler.coverage

    def reload_coverage(self) -> None:
        """Rebuild coverage from current settings."""
        self._scheduler.reload_coverage()

    async def start(self) -> None:
        """Start the scheduled broadcast cycle."""
        await self._scheduler.start()

    async def stop(self) -> None:
        """Stop the scheduled broadcast cycle."""
        await self._scheduler.stop()

    # -- Legacy 0x01 refresh request handler --------------------------------

    async def broadcast_region(self, region_id: int, request_type: int = 3) -> None:
        """Broadcast data for a specific region (triggered by 0x01 refresh request).

        This is the legacy v1 refresh-request path. iOS clients should
        use 0x02 data requests instead; this handler remains for backward
        compat with v1 clients.

        request_type: 1=radar only, 2=warnings only, 3=both.
        """
        now = time.time()
        last = self._last_refresh.get(region_id, 0)
        cooldown = settings.meshwx_refresh_cooldown
        if now - last < cooldown:
            remaining = int(cooldown - (now - last))
            logger.info(
                "Refresh for region 0x%X throttled — cooldown %ds, retry in %ds",
                region_id, cooldown, remaining,
            )
            activity_log.record(EventDir.IN, "throttled",
                f"Region 0x{region_id:X} refresh throttled ({remaining}s remaining)",
                {"region_id": region_id, "cooldown": cooldown, "remaining": remaining})
            return
        self._last_refresh[region_id] = now

        # Reuse the scheduler's cached radar composite when available;
        # otherwise refresh it via a one-off fetch.
        if request_type in (1, 3):
            await self._scheduler._refresh_radar()
            if self._scheduler._latest_radar is not None:
                from meshcore_weather.protocol.meshwx import pack_radar_compressed, REGIONS
                img_data, ts_min = self._scheduler._latest_radar
                # Use the broadcast config's grid size (portal-editable),
                # falling back to the env-var default.
                cfg = self._scheduler.current_config()
                grid_size = getattr(cfg, "radar_grid_size", None) or settings.meshwx_radar_grid_size
                if grid_size not in (16, 32, 64):
                    grid_size = 32
                grid = extract_region_grid(img_data, region_id, grid_size=grid_size)
                if grid:
                    region = REGIONS[region_id]
                    msgs = pack_radar_compressed(
                        region_id=region_id,
                        timestamp_utc_min=ts_min,
                        scale_km=region["scale"],
                        grid=grid,
                        grid_size=grid_size,
                    )
                    for i, msg in enumerate(msgs):
                        await self.radio.send_binary_channel(cobs_encode(msg))
                        if i + 1 < len(msgs):
                            await asyncio.sleep(TX_SPACING)

        if request_type in (2, 3):
            # Broadcast all active warnings in the scheduler's coverage
            warnings = extract_active_warnings(
                self.store, coverage=self._scheduler.coverage
            )
            msgs = warnings_to_binary(warnings)
            for i, msg in enumerate(msgs):
                await self.radio.send_binary_channel(cobs_encode(msg))
                if i + 1 < len(msgs):
                    await asyncio.sleep(TX_SPACING)

        req_label = {1: "radar", 2: "warnings", 3: "radar+warnings"}.get(request_type, str(request_type))
        activity_log.record(EventDir.IN, "v1_refresh",
            f"Region 0x{region_id:X} refresh ({req_label})",
            {"region_id": region_id, "request_type": request_type})
        logger.info("MeshWX refresh for region 0x%X (type=%d)", region_id, request_type)

    # Response cache for reliability over multi-hop mesh
    # Key: f"{data_type}:{loc_key}"
    # Value: (timestamp_unix, raw_wire_bytes)
    # Cached responses are re-broadcast on retry within TTL instead of
    # being silently dropped. TTL matches the old rate-limit window.
    _V2_CACHE_TTL_SECONDS = 300           # 5 minutes
    # How many times to transmit each on-demand response. Multi-hop mesh
    # flood routing can drop a single broadcast, so we send each response
    # twice with a short gap to give far-away clients a second chance.
    _V2_RESEND_COUNT = 2
    _V2_RESEND_GAP_SECONDS = 3

    async def respond_to_data_request(self, req: dict) -> None:
        """Handle a v2 data request (0x02) and broadcast the response.

        Reliability model:
          - Fresh request: build the message, cache it, send it N times
          - Retry within TTL: re-send the cached message N times (no rebuild)
          - After TTL: treat as fresh request again

        This gives far-away clients multiple chances to receive the
        broadcast through the mesh — each retry by the client is a real
        re-transmission, not a silent drop. Rebuilding is still gated by
        TTL so the encoder pipeline isn't thrashed.
        """
        data_type = req["data_type"]
        loc = req["location"]

        loc_key = self._location_key(loc)
        cache_key = f"{data_type}:{loc_key}"
        now = time.time()

        _dt_names = {0: "wx", 1: "forecast", 2: "outlook", 3: "storm_reports",
                     4: "rain_obs", 5: "metar", 6: "taf", 7: "warnings_near"}
        dt_name = _dt_names.get(data_type, f"0x{data_type:02x}")
        activity_log.record(EventDir.IN, "v2_request",
            f"Data request: {dt_name} for {loc_key}",
            {"data_type": data_type, "data_type_name": dt_name, "location": loc_key})

        if not hasattr(self, "_v2_cache"):
            self._v2_cache: dict[str, tuple[float, bytes]] = {}

        # Hit the cache? Rebroadcast the existing bytes — no rebuild needed.
        cached = self._v2_cache.get(cache_key)
        if cached and (now - cached[0]) < self._V2_CACHE_TTL_SECONDS:
            cached_msg = cached[1]
            logger.info(
                "Re-broadcasting cached v2 response type=0x%02x for %s (%d bytes, %ds old)",
                cached_msg[0], cache_key, len(cached_msg), int(now - cached[0]),
            )
            await self._transmit_response(cached_msg)
            return

        # Cache miss or TTL expired — build a fresh response.
        location_name = self._location_to_query_string(loc)
        if not location_name:
            logger.info(
                "v2 request for %s: location unresolvable — sending NOT_AVAILABLE",
                cache_key,
            )
            await self._emit_not_available(
                data_type, REASON_LOCATION_UNRESOLVABLE, loc, cache_key, now,
            )
            return

        msg = None
        builder_exception: Exception | None = None
        try:
            if data_type == DATA_WX:
                msg = self._build_observation(loc, location_name)
            elif data_type == DATA_FORECAST:
                msg = self._build_forecast(loc, location_name)
            elif data_type == DATA_METAR:
                msg = self._build_metar(loc, location_name)
            elif data_type == DATA_OUTLOOK:
                msg = self._build_outlook(loc, location_name)
            elif data_type == DATA_STORM_REPORTS:
                msg = self._build_storm_reports(loc, location_name)
            elif data_type == DATA_RAIN_OBS:
                msg = self._build_rain_obs(loc, location_name)
            elif data_type == DATA_TAF:
                msg = self._build_taf(loc, location_name)
            elif data_type == DATA_WARNINGS_NEAR:
                msg = self._build_warnings_near(loc, location_name)
            elif data_type == DATA_SPACE_WEATHER:
                from meshcore_weather.core import space_weather
                sw = space_weather.space_weather_for(self.store)
                msg = sw.to_bytes() if sw else None
            elif data_type == DATA_WARNING_DETAIL:
                msgs = self._build_warning_detail(loc, location_name)
                if msgs:
                    for m in msgs:
                        await self._transmit_response(m)
                        self._v2_cache[cache_key] = (now, m)
                    activity_log.record(EventDir.OUT, "v2_response",
                        f"Warning detail: {len(msgs)} chunk(s) for {loc_key}",
                        {"data_type": data_type, "location": loc_key, "chunks": len(msgs)})
                    return
                msg = None
            else:
                logger.info(
                    "v2 request for %s: unsupported data_type %d — sending NOT_AVAILABLE",
                    cache_key, data_type,
                )
                await self._emit_not_available(
                    data_type, REASON_PRODUCT_UNSUPPORTED, loc, cache_key, now,
                )
                return
        except Exception as exc:
            builder_exception = exc
            logger.exception("v2 builder raised for %s", cache_key)

        if builder_exception is not None:
            await self._emit_not_available(
                data_type, REASON_BOT_ERROR, loc, cache_key, now,
            )
            return

        if msg is None:
            logger.info(
                "v2 request for %s: no data available — sending NOT_AVAILABLE",
                cache_key,
            )
            await self._emit_not_available(
                data_type, REASON_NO_DATA, loc, cache_key, now,
            )
            return

        # Cache the built message so retries within TTL rebroadcast it
        self._v2_cache[cache_key] = (now, msg)
        logger.info(
            "Sent v2 response type=0x%02x for %s (%d bytes)",
            msg[0], cache_key, len(msg),
        )
        activity_log.record(EventDir.OUT, "v2_response",
            f"Response: {dt_name} for {loc_key} ({len(msg)}B)",
            {"data_type_name": dt_name, "location": loc_key, "bytes": len(msg), "msg_type": f"0x{msg[0]:02x}"})
        activity_log.record_send(1, len(msg))
        await self._transmit_response(msg)

    async def _emit_not_available(
        self,
        data_type: int,
        reason: int,
        loc: dict,
        cache_key: str,
        now: float,
    ) -> None:
        """Send a 0x03 MSG_NOT_AVAILABLE telling the client we can't serve
        the request. Cached and double-transmitted like any other response
        so retries within TTL will re-emit the same NOT_AVAILABLE without
        re-running the expensive resolver + builder path.
        """
        loc_type = loc.get("type")
        if loc_type == LOC_ZONE:
            loc_id = loc.get("zone", "")
        elif loc_type == LOC_STATION:
            loc_id = loc.get("station", "")
        elif loc_type == LOC_PFM_POINT:
            loc_id = loc.get("pfm_point_id", 0)
        elif loc_type == LOC_PLACE:
            loc_id = loc.get("place_id", 0)
        elif loc_type == LOC_WFO:
            loc_id = loc.get("wfo", "")
        elif loc_type == LOC_LATLON:
            loc_id = (loc.get("lat", 0.0), loc.get("lon", 0.0))
        else:
            # Unknown — use zero-valued placeholder so pack never raises.
            loc_type = LOC_ZONE
            loc_id = "AKZ000"  # minimal valid zone code
        try:
            msg = pack_not_available(data_type, reason, loc_type, loc_id)
        except Exception:
            logger.exception("failed to pack NOT_AVAILABLE for %s", cache_key)
            return
        # Cache so retries within TTL reuse this same NOT_AVAILABLE
        self._v2_cache[cache_key] = (now, msg)
        await self._transmit_response(msg)

    async def _transmit_response(self, msg: bytes) -> None:
        """Send a response on the data channel.

        Sends the v3 message directly (COBS-encoded) without v4 wrapping
        to avoid exceeding the companion radio's 160-byte channel MTU.
        Sends N times for reliability over multi-hop mesh.
        """
        cobs_msg = cobs_encode(msg)
        for i in range(self._V2_RESEND_COUNT):
            try:
                await self.radio.send_binary_channel(cobs_msg)
            except Exception:
                logger.exception("v2 response send failed on transmission %d", i + 1)
                return
            if i + 1 < self._V2_RESEND_COUNT:
                await asyncio.sleep(self._V2_RESEND_GAP_SECONDS)

    def _location_key(self, loc: dict) -> str:
        """Stable string key for a location (used for rate limiting)."""
        t = loc.get("type")
        if t == LOC_ZONE:
            return f"zone:{loc.get('zone')}"
        if t == LOC_STATION:
            return f"station:{loc.get('station')}"
        if t == LOC_PFM_POINT:
            return f"pfm:{loc.get('pfm_point_id')}"
        if t == LOC_PLACE:
            return f"place:{loc.get('place_id')}"
        return str(loc)

    def _location_to_query_string(self, loc: dict) -> str | None:
        """Convert a location dict to a string the WeatherStore can resolve.

        For LOC_PFM_POINT, looks up the index in the bundled pfm_points.json
        and returns the point's canonical zone code so the existing ZFP-based
        forecast path can use it directly.
        """
        t = loc.get("type")
        if t == LOC_ZONE:
            return loc.get("zone")
        if t == LOC_STATION:
            return loc.get("station")
        if t == LOC_PFM_POINT:
            # Delegate to the scheduler's pfm_points list (loaded once at
            # startup). Falls back to an empty list if the scheduler hasn't
            # started yet, which is fine — the request just returns None.
            points = self._scheduler._pfm_points
            idx = loc.get("pfm_point_id")
            if idx is None or idx < 0 or idx >= len(points):
                logger.debug("PFM point index %s out of range (0..%d)", idx, len(points))
                return None
            return points[idx].get("zone")
        if t == LOC_PLACE:
            # Resolve place index to zone info via geodata
            resolved = resolver.resolve_by_place_index(loc.get("place_id", -1))
            if resolved and resolved.get("zones"):
                return resolved["zones"][0]
            return None
        return None

    def _build_observation(self, loc: dict, query: str) -> bytes | None:
        """0x30 observation via core.services (same parse as the scheduler
        and the text commands). Echoes LOC_PLACE back for place/PFM-point
        requests so the client shows a city name."""
        from meshcore_weather.core import services
        resolved = resolver.resolve(query)
        if not resolved:
            return None
        resp_loc_type, resp_loc_id = self._echo_location(loc)
        ob = services.observation_for(self.store, resolved)
        if ob is None:
            return None
        return ob.to_bytes(loc_type=resp_loc_type, loc_id=resp_loc_id)

    def _echo_location(self, loc: dict) -> tuple[int | None, object]:
        """Location to echo in a response: the exact place_id for LOC_PLACE
        requests, the nearest place for LOC_PFM_POINT, else None (station)."""
        loc_type = loc.get("type")
        if loc_type == LOC_PLACE:
            return LOC_PLACE, loc.get("place_id")
        if loc_type == LOC_PFM_POINT:
            idx = loc.get("pfm_point_id")
            points = self._scheduler._pfm_points
            if idx is not None and 0 <= idx < len(points):
                pt = points[idx]
                place_idx = resolver.find_place_index(pt["lat"], pt["lon"])
                if place_idx is not None:
                    return LOC_PLACE, place_idx
        return None, None

    def _build_forecast(self, loc: dict, query: str) -> bytes | None:
        """0x31 forecast via core.services.forecast_for (nearest PFM point by
        distance). Falls back to the scheduler's ZFP/SFT path if no PFM
        point is within range."""
        from meshcore_weather.core import services
        resolved = resolver.resolve(query)
        if not resolved:
            return None
        resp_loc_type, resp_loc_id = self._echo_location(loc)
        fc = services.forecast_for(self.store, resolved)
        if fc is not None:
            return fc.to_bytes(loc_type=resp_loc_type, loc_id=resp_loc_id)
        # Fallback: reuse the scheduler builder (ZFP narrative / SFT table).
        from meshcore_weather.schedule.executor import _build_forecast as _exec_forecast
        from meshcore_weather.schedule.models import BroadcastJob
        job = BroadcastJob(id="ondemand", name="ondemand", product="forecast",
                           location_type="city", location_id=query, interval_minutes=1)
        msgs = _exec_forecast(job, self._scheduler_ctx())
        return msgs[0] if msgs else None

    def _scheduler_ctx(self):
        from meshcore_weather.schedule.executor import ExecutorContext
        s = self._scheduler
        return ExecutorContext(
            store=self.store, coverage=s._coverage, pfm_points=s._pfm_points,
            latest_radar=s._latest_radar, latest_ridge=s._latest_ridge,
            last_broadcast_warnings=s._warning_tracking,
        )

    def _build_metar(self, loc: dict, query: str) -> bytes | None:
        """0x35 METAR: the observation service (same 0x30 wire format)."""
        return self._build_observation(loc, query)

    def _build_outlook(self, loc: dict, query: str) -> bytes | None:
        from meshcore_weather.core import services
        resolved = resolver.resolve(query)
        ol = services.outlook_for(self.store, resolved) if resolved else None
        return ol.to_bytes() if ol else None

    def _build_storm_reports(self, loc: dict, query: str) -> bytes | None:
        from meshcore_weather.core import services
        resolved = resolver.resolve(query)
        sr = services.storm_reports_for(self.store, resolved) if resolved else None
        return sr.to_bytes() if sr else None

    def _build_rain_obs(self, loc: dict, query: str) -> bytes | None:
        from meshcore_weather.core import services
        resolved = resolver.resolve(query)
        ro = services.rain_for(self.store, resolved) if resolved else None
        return ro.to_bytes() if ro else None

    def _build_taf(self, loc: dict, query: str) -> bytes | None:
        from meshcore_weather.core import services
        if loc.get("type") == LOC_STATION and loc.get("station"):
            resolved = resolver.resolve(loc["station"])
        else:
            resolved = resolver.resolve(query)
        tf = services.taf_for(self.store, resolved) if resolved else None
        return tf.to_bytes() if tf else None

    def _build_warnings_near(self, loc: dict, query: str) -> bytes | None:
        """0x37 warnings-near via core.services.warnings_for. A bare zone
        code (including marine zones not in zones.json) is honoured directly."""
        from meshcore_weather.core import services
        resolved = None
        if loc.get("type") == LOC_ZONE and loc.get("zone"):
            z = loc["zone"]
            resolved = resolver.resolve(z) or {"zones": [z], "lat": None, "lon": None, "name": z}
        if resolved is None:
            resolved = resolver.resolve(query)
        if not resolved or not resolved.get("zones"):
            return None
        zone = resolved["zones"][0]
        nearby: list[dict] = []
        for w in services.warnings_for(self.store, resolved):
            ugcs = set(w.get("ugcs") or w.get("zones", []))
            entry_zone = zone if zone in ugcs else (sorted(ugcs)[0] if ugcs else "")
            expires_at = w.get("expires_at")
            nearby.append({
                "event": w.get("event_code", 0),
                "expires_unix_min": int(expires_at.timestamp() / 60) if expires_at else 0,
                "zone": entry_zone if len(entry_zone) == 6 and entry_zone[2] == "Z" else "",
            })
        if not nearby:
            return None
        return pack_warnings_near(LOC_ZONE, zone, nearby)

    def _build_warning_detail(self, loc: dict, query: str) -> list[bytes] | None:
        """Build 0x40 text chunks with warning description for a zone.

        The client sends this request after receiving a compact 0x20/0x21
        warning and wanting the full detail (wind, humidity, impacts, etc.).
        Returns text chunks for all warnings affecting the zone.
        """
        from meshcore_weather.protocol.meshwx import (
            LOC_WFO, TEXT_SUBJECT_GENERAL, pack_text_chunks,
        )

        zone = ""
        if loc.get("type") == LOC_ZONE:
            zone = loc.get("zone", "")
        if not zone:
            resolved = resolver.resolve(query)
            if not resolved:
                return None
            zones_list = resolved.get("zones") or []
            if not zones_list:
                return None
            zone = zones_list[0]

        all_warnings = extract_active_warnings(self.store, coverage=None)

        descriptions: list[str] = []
        for w in all_warnings:
            ugcs = set(w.get("ugcs") or w.get("zones", []))
            if zone not in ugcs:
                continue
            desc = w.get("description", "")
            headline = w.get("headline", "")
            if desc:
                descriptions.append(f"{headline}\n{desc}" if headline else desc)
            elif headline:
                descriptions.append(headline)

        if not descriptions:
            return None

        full_text = "\n---\n".join(descriptions)
        wfo = "UNK"
        # Use the first warning's office
        for w in all_warnings:
            if w.get("vtec_office"):
                wfo = w["vtec_office"]
                break

        return pack_text_chunks(
            subject_type=TEXT_SUBJECT_GENERAL,
            loc_type=LOC_WFO,
            loc_id=wfo,
            text=full_text,
        )
