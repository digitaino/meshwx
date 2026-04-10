"""MeshWX broadcast loop: periodically sends binary weather data on the data channel."""

import asyncio
import logging
import time

import httpx

from meshcore_weather.config import settings
from meshcore_weather.geodata import resolver
from meshcore_weather.meshcore.radio import MeshcoreRadio
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol.coverage import Coverage
from meshcore_weather.protocol.encoders import (
    encode_forecast_from_zfp,
    encode_metar,
    encode_rwr_city,
    now_utc_minutes,
)
from meshcore_weather.protocol.meshwx import (
    DATA_FORECAST,
    DATA_METAR,
    DATA_WX,
    LOC_STATION,
    LOC_ZONE,
    cobs_encode,
)
from meshcore_weather.protocol.radar import fetch_radar_composite, build_radar_messages
from meshcore_weather.protocol.warnings import extract_active_warnings, warnings_to_binary

logger = logging.getLogger(__name__)

# Delay between consecutive LoRa transmissions (seconds)
TX_SPACING = 2


class MeshWXBroadcaster:
    """Broadcasts binary weather data on the MeshWX data channel."""

    def __init__(self, store: WeatherStore, radio: MeshcoreRadio):
        self.store = store
        self.radio = radio
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_refresh: dict[int, float] = {}  # region_id -> timestamp
        self._http_client: httpx.AsyncClient | None = None
        self._latest_radar: tuple[bytes, int] | None = None  # (img_bytes, ts_min)
        self._coverage: Coverage = Coverage.empty()

    def reload_coverage(self) -> None:
        """Rebuild coverage from current settings. Called on startup + config changes."""
        self._coverage = Coverage.from_config()
        logger.info("Coverage: %s", self._coverage.summary())

    @property
    def coverage(self) -> Coverage:
        return self._coverage

    async def start(self) -> None:
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self.reload_coverage()
        self._task = asyncio.create_task(self._broadcast_loop())
        logger.info("MeshWX broadcaster started (interval=%ds)", settings.meshwx_broadcast_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._http_client:
            await self._http_client.aclose()

    async def _broadcast_loop(self) -> None:
        # Wait a bit on startup before first broadcast
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._broadcast_all()
            except Exception:
                logger.exception("Error in MeshWX broadcast cycle")
            await asyncio.sleep(settings.meshwx_broadcast_interval)

    async def _broadcast_all(self) -> None:
        """Run one full broadcast cycle: radar + warnings."""
        radar_count = await self._broadcast_radar()
        warn_count = await self._broadcast_warnings()
        if radar_count or warn_count:
            logger.info("MeshWX broadcast: %d radar grid(s), %d warning(s)",
                        radar_count, warn_count)

    async def _fetch_radar(self) -> None:
        """Fetch latest radar composite from IEM."""
        if not self._http_client:
            return
        result = await fetch_radar_composite(self._http_client)
        if result:
            self._latest_radar = result

    async def _broadcast_radar(self) -> int:
        """Fetch and broadcast COBS-encoded radar grids (filtered by coverage)."""
        await self._fetch_radar()
        if not self._latest_radar:
            return 0
        img_data, ts_min = self._latest_radar
        region_ids = self._coverage.region_ids if not self._coverage.is_empty() else None
        msgs = build_radar_messages(img_data, ts_min, region_ids=region_ids)
        sent = 0
        for msg in msgs:
            await self.radio.send_binary_channel(cobs_encode(msg))
            sent += 1
            await asyncio.sleep(TX_SPACING)
        return sent

    async def _broadcast_warnings(self) -> int:
        """Broadcast COBS-encoded warning polygons (filtered by coverage)."""
        warnings = extract_active_warnings(self.store, coverage=self._coverage)
        msgs = warnings_to_binary(warnings)
        sent = 0
        for msg in msgs:
            await self.radio.send_binary_channel(cobs_encode(msg))
            sent += 1
            if sent < len(msgs):
                await asyncio.sleep(TX_SPACING)
        return sent

    async def broadcast_region(self, region_id: int, request_type: int = 3) -> None:
        """Broadcast data for a specific region (triggered by refresh request).

        request_type: 1=radar only, 2=warnings only, 3=both.
        """
        now = time.time()
        last = self._last_refresh.get(region_id, 0)
        if now - last < settings.meshwx_refresh_cooldown:
            logger.debug("Refresh for region 0x%X throttled (cooldown)", region_id)
            return
        self._last_refresh[region_id] = now

        if request_type in (1, 3):
            await self._fetch_radar()
            if self._latest_radar:
                from meshcore_weather.protocol.radar import extract_region_grid
                from meshcore_weather.protocol.meshwx import pack_radar_grid, REGIONS
                img_data, ts_min = self._latest_radar
                grid = extract_region_grid(img_data, region_id)
                if grid:
                    region = REGIONS[region_id]
                    msg = pack_radar_grid(region_id, 0, ts_min, region["scale"], grid)
                    await self.radio.send_binary_channel(cobs_encode(msg))

        if request_type in (2, 3):
            await self._broadcast_warnings()

        logger.info("MeshWX refresh for region 0x%X (type=%d)", region_id, request_type)

    async def respond_to_data_request(self, req: dict) -> None:
        """Handle a v2 data request (0x02) and broadcast the response.

        Rate-limited per (data_type, location) for 5 minutes.
        Looks up the right data from the weather store and encodes it
        using the v2 encoders, then broadcasts on the data channel.
        """
        data_type = req["data_type"]
        loc = req["location"]

        # Rate-limit key
        loc_key = self._location_key(loc)
        rate_key = f"{data_type}:{loc_key}"
        now = time.time()
        if not hasattr(self, "_v2_rate_limit"):
            self._v2_rate_limit: dict[str, float] = {}
        last = self._v2_rate_limit.get(rate_key, 0)
        if now - last < 300:  # 5 min per (type, location)
            logger.debug("v2 request throttled: %s", rate_key)
            return
        self._v2_rate_limit[rate_key] = now

        # Resolve the location dict to something the store can query
        location_name = self._location_to_query_string(loc)
        if not location_name:
            logger.debug("Could not resolve location for v2 request: %s", loc)
            return

        msg = None
        if data_type == DATA_WX:
            msg = self._build_observation(loc, location_name)
        elif data_type == DATA_FORECAST:
            msg = self._build_forecast(loc, location_name)
        elif data_type == DATA_METAR:
            msg = self._build_metar(loc, location_name)
        else:
            logger.debug("Unsupported v2 data type: %d", data_type)
            return

        if msg is None:
            logger.info("No v2 response data available for %s", rate_key)
            return

        await self.radio.send_binary_channel(cobs_encode(msg))
        logger.info("Sent v2 response type=0x%02x for %s (%d bytes)",
                    msg[0], rate_key, len(msg))

    @staticmethod
    def _location_key(loc: dict) -> str:
        """Stable string key for a location (used for rate limiting)."""
        t = loc.get("type")
        if t == LOC_ZONE:
            return f"zone:{loc.get('zone')}"
        if t == LOC_STATION:
            return f"station:{loc.get('station')}"
        return str(loc)

    @staticmethod
    def _location_to_query_string(loc: dict) -> str | None:
        """Convert a location dict to a string the WeatherStore can resolve."""
        t = loc.get("type")
        if t == LOC_ZONE:
            return loc.get("zone")
        if t == LOC_STATION:
            return loc.get("station")
        return None

    def _build_observation(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x30 observation message for the given location."""
        resolved = resolver.resolve(query)
        if not resolved:
            return None

        station = resolved.get("station")
        if station:
            raw = self.store._find_metar_raw(station)
            if raw:
                metar_text, _ts = raw
                msg = encode_metar(station, metar_text, now_utc_minutes())
                if msg:
                    return msg

        # Fall back: try RWR via WFO
        zones = resolved.get("zones", [])
        if zones:
            zone = zones[0]
            for wfo in resolved.get("wfos", []):
                state = zone[:2]
                rwr = self.store._find("RWR", f"{wfo}{state}")
                if rwr:
                    city = resolved["name"].split(",")[0].strip().upper()
                    line = self.store._parse_rwr_city_raw(rwr.raw_text, city)
                    if line:
                        return encode_rwr_city(zone, line, now_utc_minutes())
        return None

    def _build_forecast(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x31 forecast message for the given location."""
        resolved = resolver.resolve(query)
        if not resolved:
            return None
        zones = resolved.get("zones", [])
        if not zones:
            return None
        zone = zones[0]

        # Find the ZFP for this zone via its WFO
        for wfo in resolved.get("wfos", []):
            state = zone[:2]
            zfp = self.store._find("ZFP", f"{wfo}{state}")
            if zfp:
                from meshcore_weather.parser.weather import _age_str  # noqa
                zone_text = self.store._parse_zfp_zone(zfp.raw_text, zone)
                if zone_text:
                    # _parse_zfp_zone returns a single period string; we need
                    # the full text. Fall back to the raw product text.
                    import math
                    hours_ago = int(
                        (now_utc_minutes() - zfp.timestamp.hour * 60 - zfp.timestamp.minute) / 60
                    )
                    return encode_forecast_from_zfp(
                        zone, zfp.raw_text, max(0, hours_ago),
                    )
        return None

    def _build_metar(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x35 METAR message (uses 0x30 observation format)."""
        # For now, same as observation for station queries
        return self._build_observation(loc, query)
