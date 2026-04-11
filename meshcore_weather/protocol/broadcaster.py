"""MeshWX broadcast loop: periodically sends binary weather data on the data channel."""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

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
    DATA_FORECAST,
    DATA_METAR,
    DATA_OUTLOOK,
    DATA_RAIN_OBS,
    DATA_STORM_REPORTS,
    DATA_TAF,
    DATA_WARNINGS_NEAR,
    DATA_WX,
    LOC_PFM_POINT,
    LOC_STATION,
    LOC_ZONE,
    SEV_ADVISORY,
    SEV_WARNING,
    SEV_WATCH,
    cobs_encode,
    pack_warnings_near,
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
        self._pfm_points: list[dict] | None = None  # loaded lazily from bundle
        # client_data/ ships inside the package (see pyproject.toml
        # package-data), so it travels with the pip install regardless of
        # deployment location (Docker, bare install, editable mode).
        # __file__ = .../meshcore_weather/protocol/broadcaster.py
        # .parent.parent = .../meshcore_weather
        self._pfm_points_path = (
            Path(__file__).resolve().parent.parent
            / "client_data"
            / "pfm_points.json"
        )

    def _load_pfm_points(self) -> list[dict]:
        """Load pfm_points.json once and cache. Returns empty list if missing."""
        if self._pfm_points is not None:
            return self._pfm_points
        if not self._pfm_points_path.exists():
            logger.warning(
                "pfm_points.json not found at %s — LOC_PFM_POINT requests will fail",
                self._pfm_points_path,
            )
            self._pfm_points = []
            return self._pfm_points
        try:
            data = json.loads(self._pfm_points_path.read_text())
            # Compact array form: [[name, wfo, lat, lon, zone], ...]
            points = [
                {"name": p[0], "wfo": p[1], "lat": p[2], "lon": p[3], "zone": p[4]}
                for p in data.get("points", [])
            ]
            self._pfm_points = points
            logger.info("Loaded %d PFM points from bundle", len(points))
            return self._pfm_points
        except Exception as exc:
            logger.warning("Failed to load pfm_points.json: %s", exc)
            self._pfm_points = []
            return self._pfm_points

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
        """Run one full broadcast cycle: radar + warnings + home obs/forecast."""
        radar_count = await self._broadcast_radar()
        warn_count = await self._broadcast_warnings()
        home_count = await self._broadcast_home_locations()
        if radar_count or warn_count or home_count:
            logger.info(
                "MeshWX broadcast: %d radar grid(s), %d warning(s), %d home msg(s)",
                radar_count, warn_count, home_count,
            )

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

    async def _broadcast_home_locations(self) -> int:
        """Broadcast a 0x30 observation and 0x31 forecast for each home city.

        Iterates through `settings.home_cities` (the operator-configured
        list), resolves each city, builds the obs + forecast messages with
        the same builders the v2 data-request path uses, and sends them on
        the data channel.

        For forecasts we prefer LOC_PFM_POINT (the same location type the
        iOS city-search flow uses) so a client that has saved the city
        from autocomplete will recognize the broadcast and update its
        cached forecast without ever sending a request. Falls back to
        LOC_ZONE if the city has no nearby PFM point.

        For observations we use LOC_ZONE since RWR/METAR data is
        zone/station-keyed; clients should index their cache by both zone
        code and pfm_point_id so an obs broadcast can update an entry the
        client originally saved by city search.
        """
        from meshcore_weather.config import settings

        cities_csv = (settings.home_cities or "").strip()
        if not cities_csv:
            return 0

        cities = [c.strip() for c in cities_csv.split(",") if c.strip()]
        if not cities:
            return 0

        sent = 0
        for city in cities:
            resolved = resolver.resolve(city)
            if not resolved:
                logger.debug("home broadcast: could not resolve %r", city)
                continue
            zones = resolved.get("zones") or []
            if not zones:
                logger.debug("home broadcast: %r has no zones", city)
                continue
            zone = zones[0]

            # 0x30 Observation — keyed by zone (RWR fallback) or station (METAR)
            obs_loc = {"type": LOC_ZONE, "zone": zone}
            obs_msg = self._build_observation(obs_loc, city)
            if obs_msg:
                await self.radio.send_binary_channel(cobs_encode(obs_msg))
                sent += 1
                logger.debug("home broadcast: sent obs for %s (%d bytes)", city, len(obs_msg))
                await asyncio.sleep(TX_SPACING)

            # 0x31 Forecast — prefer LOC_PFM_POINT so iOS city-search clients
            # match the broadcast. Find the nearest PFM point to the city.
            pfm_idx = self._nearest_pfm_point_index(
                resolved.get("lat", 0.0), resolved.get("lon", 0.0)
            )
            if pfm_idx is not None:
                fc_loc = {"type": LOC_PFM_POINT, "pfm_point_id": pfm_idx}
            else:
                fc_loc = {"type": LOC_ZONE, "zone": zone}
            fc_msg = self._build_forecast(fc_loc, city)
            if fc_msg:
                await self.radio.send_binary_channel(cobs_encode(fc_msg))
                sent += 1
                logger.debug("home broadcast: sent fcst for %s (%d bytes)", city, len(fc_msg))
                await asyncio.sleep(TX_SPACING)

        return sent

    def _nearest_pfm_point_index(self, lat: float, lon: float) -> int | None:
        """Return the array index of the nearest PFM point to (lat, lon),
        or None if pfm_points.json isn't available or no point is within
        a reasonable distance (~50 km).
        """
        points = self._load_pfm_points()
        if not points:
            return None
        best_idx: int | None = None
        best_d2 = float("inf")
        for i, p in enumerate(points):
            dlat = lat - p["lat"]
            dlon = lon - p["lon"]
            d2 = dlat * dlat + dlon * dlon
            if d2 < best_d2:
                best_d2 = d2
                best_idx = i
        # Quick distance gate (~50 km = 0.45° squared = 0.2)
        if best_d2 > 0.2:
            return None
        return best_idx

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
        else:
            logger.debug("Unsupported v2 data type: %d", data_type)
            return

        if msg is None:
            logger.info("No v2 response data available for %s", rate_key)
            return

        await self.radio.send_binary_channel(cobs_encode(msg))
        logger.info("Sent v2 response type=0x%02x for %s (%d bytes)",
                    msg[0], rate_key, len(msg))

    def _location_key(self, loc: dict) -> str:
        """Stable string key for a location (used for rate limiting)."""
        t = loc.get("type")
        if t == LOC_ZONE:
            return f"zone:{loc.get('zone')}"
        if t == LOC_STATION:
            return f"station:{loc.get('station')}"
        if t == LOC_PFM_POINT:
            return f"pfm:{loc.get('pfm_point_id')}"
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
            points = self._load_pfm_points()
            idx = loc.get("pfm_point_id")
            if idx is None or idx < 0 or idx >= len(points):
                logger.debug("PFM point index %s out of range (0..%d)", idx, len(points))
                return None
            # Use the canonical zone from the PFM point for ZFP lookup.
            # Phase 2 will swap this for a direct PFM product lookup, but the
            # wire format won't change.
            return points[idx].get("zone")
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
        """Build a 0x31 forecast message for the given location.

        Tries PFM (canonical NWS Point Forecast Matrix, structured numeric
        data) first via encode_forecast_from_pfm, then falls back to ZFP
        narrative parsing if no PFM product is available for the zone.
        Same 0x31 wire format on the output regardless of source — the
        client sees no difference, just better data quality when PFM is
        available.

        For LOC_PFM_POINT requests, the response carries LOC_PFM_POINT in
        its location field (not LOC_ZONE) so the client can correlate the
        broadcast with its original request.

        Uses _build_origs() + _find_any_orig() to handle the SJU→JSJ
        (San Juan PR) and GUM→GUA (Guam) AWIPS aliases — the resolver
        returns the canonical WFO code but EMWIN product filenames use
        the AWIPS alias.
        """
        resolved = resolver.resolve(query)
        if not resolved:
            return None
        zones = resolved.get("zones", [])
        if not zones:
            return None
        zone = zones[0]

        # If this was a LOC_PFM_POINT request, pass the PFM point ID through
        # to the encoder so the response echoes the requested location type.
        resp_loc_type = None
        resp_loc_id = None
        if loc.get("type") == LOC_PFM_POINT:
            resp_loc_type = LOC_PFM_POINT
            resp_loc_id = loc.get("pfm_point_id")

        origs = self.store._build_origs(resolved)

        # Primary path: PFM (structured numeric forecast data)
        pfm = self.store._find_any_orig("PFM", origs)
        if pfm:
            hours_ago = int(
                (now_utc_minutes() - pfm.timestamp.hour * 60 - pfm.timestamp.minute) / 60
            )
            msg = encode_forecast_from_pfm(
                pfm.raw_text, zone, max(0, hours_ago),
                loc_type=resp_loc_type,
                loc_id=resp_loc_id,
            )
            if msg is not None:
                logger.debug("forecast: PFM source for %s", zone)
                return msg
            logger.debug("forecast: PFM found for %s but no usable data", zone)

        # Fallback: ZFP narrative parsing
        zfp = self.store._find_any_orig("ZFP", origs)
        if zfp:
            zone_text = self.store._parse_zfp_zone(zfp.raw_text, zone)
            if zone_text:
                hours_ago = int(
                    (now_utc_minutes() - zfp.timestamp.hour * 60 - zfp.timestamp.minute) / 60
                )
                logger.debug("forecast: ZFP fallback for %s", zone)
                return encode_forecast_from_zfp(
                    zone, zfp.raw_text, max(0, hours_ago),
                    loc_type=resp_loc_type,
                    loc_id=resp_loc_id,
                )
        return None

    def _build_metar(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x35 METAR message (uses 0x30 observation format)."""
        # For now, same as observation for station queries
        return self._build_observation(loc, query)

    def _build_outlook(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x32 Hazardous Weather Outlook for the given location.

        Looks up the latest HWO product for the location's WFO and runs
        encode_hwo() to produce the outlook message. HWOs are typically
        issued once daily by each WFO and cover days 1-7.
        """
        resolved = resolver.resolve(query)
        if not resolved:
            return None
        zones = resolved.get("zones") or []
        if not zones:
            return None
        zone = zones[0]

        origs = self.store._build_origs(resolved)
        hwo = self.store._find_any_orig("HWO", origs)
        if hwo is None:
            # Wider fallback: any HWO whose UGC line covers our zone
            from meshcore_weather.parser.weather import _expand_zone_ranges
            loc_zones = set(zones)
            best = None
            for prod in self.store._products.values():
                if prod.product_type != "HWO":
                    continue
                if loc_zones & _expand_zone_ranges(prod.raw_text):
                    if best is None or prod.timestamp > best.timestamp:
                        best = prod
            hwo = best
        if hwo is None:
            return None

        issued_min = hwo.timestamp.hour * 60 + hwo.timestamp.minute
        return encode_hwo(zone, hwo.raw_text, issued_min)

    def _build_storm_reports(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x33 Local Storm Reports message for the location.

        Walks LSR products in the store, filters to entries from the
        location's state, and runs encode_lsr_reports() to pack up to 16
        most recent reports into the wire format.
        """
        resolved = resolver.resolve(query)
        if not resolved:
            return None
        zones = resolved.get("zones") or []
        if not zones:
            return None
        zone = zones[0]
        state = zone[:2]

        # Collect deduplicated entries from LSR products newest first
        seen: set[str] = set()
        entries: list[dict] = []
        for prod in sorted(
            self.store._products.values(), key=lambda p: p.timestamp, reverse=True
        ):
            if prod.product_type != "LSR":
                continue
            # Filter by state. We accept either an exact filename-state match
            # or a text-derived affected state, since LSR filenames don't
            # always agree with the state of the actual report.
            if prod.state != state:
                affected = self.store._affected_state(prod)
                if affected != state:
                    continue
            for entry in self.store._parse_lsr_entries(prod.raw_text):
                # Filter to reports actually in the requested state
                if entry.get("state") and entry["state"] != state:
                    continue
                key = f"{entry.get('time','')}_{entry.get('event','')}_{entry.get('location','')}"
                if key in seen:
                    continue
                seen.add(key)
                entries.append(entry)
                if len(entries) >= 16:
                    break
            if len(entries) >= 16:
                break

        if not entries:
            return None
        return encode_lsr_reports(zone, entries, now_utc_minutes())

    def _build_rain_obs(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x34 rain observations message for the location's region.

        Scans RWR (Regional Weather Roundup) products from the location's
        WFO for cities currently reporting any form of precipitation.
        Encodes the list as 0x34 with each city referenced by its place_id.
        """
        resolved = resolver.resolve(query)
        if not resolved:
            return None
        zones = resolved.get("zones") or []
        if not zones:
            return None
        zone = zones[0]

        origs = self.store._build_origs(resolved)
        rwr = self.store._find_any_orig("RWR", origs)
        if rwr is None:
            return None

        # Scan the RWR table for cities with precipitation. Reuses the same
        # heuristic as WeatherStore.scan_rain but extracts structured data
        # instead of pre-formatted text.
        rain_keywords = {
            "RAIN", "LGT RAIN", "HVY RAIN", "TSTORM", "T-STORM",
            "DRIZZLE", "SHOWERS", "SHOWER", "SNOW",
        }
        rainy: list[dict] = []
        seen_names: set[str] = set()
        in_table = False
        for line in rwr.raw_text.splitlines():
            stripped = line.strip()
            if "SKY/WX" in stripped and "TMP" in stripped:
                in_table = True
                continue
            if not in_table or not stripped:
                continue
            if stripped.startswith("$$"):
                break
            upper = stripped.upper()
            if not any(kw in upper for kw in rain_keywords):
                continue
            parts = stripped.split()
            # Strip the leading "*" flag (NWS RWR uses it to mark significant
            # weather rows) from the first part so the city name is clean
            # for the place_id lookup.
            if parts and parts[0].startswith("*"):
                parts[0] = parts[0][1:]
            # Walk forward extracting city name until we hit a sky-word or a number
            sky_words = rain_keywords | {
                "SUNNY", "MOSUNNY", "PTSUNNY", "CLEAR", "MOCLDY", "PTCLDY",
                "CLOUDY", "FAIR", "FOG", "HAZE", "WINDY", "LGT", "HVY",
            }
            city_parts: list[str] = []
            rain_text = ""
            temp_f = 60
            for p in parts:
                if p.upper() in sky_words:
                    rain_text = p
                    break
                if p.lstrip("-").isdigit():
                    break
                city_parts.append(p)
            # First number after the sky word is the temperature
            if rain_text:
                idx = parts.index(rain_text) if rain_text in parts else -1
                for tp in parts[idx + 1 :]:
                    if tp.lstrip("-").isdigit():
                        try:
                            temp_f = int(tp)
                        except ValueError:
                            pass
                        break
            city_name = " ".join(city_parts).title().strip()
            if not city_name or city_name in seen_names:
                continue
            seen_names.add(city_name)
            rainy.append({
                "name": city_name,
                "state": zone[:2],
                "rain_text": rain_text or "rain",
                "temp_f": temp_f,
            })

        if not rainy:
            return None
        return encode_rain_cities(zone, rainy, now_utc_minutes())

    def _build_taf(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x36 TAF (Terminal Aerodrome Forecast) message.

        TAF is keyed to a station ICAO. Walks the store for any TAF product
        that contains a TAF block for the requested station, then runs
        encode_taf() to extract the BASE forecast group and pack it as 0x36.
        """
        # TAF is station-keyed. Resolve the location to a station.
        if loc.get("type") == LOC_STATION:
            station = loc.get("station")
        else:
            resolved = resolver.resolve(query)
            if not resolved:
                return None
            station = resolved.get("station")
        if not station:
            return None

        # Find a product whose text contains a TAF block for this station.
        # NWS TAF products use AFOS like "TAFEWX" with multiple stations
        # in one file, so we have to scan rather than direct-lookup.
        target_marker = f"TAF {station}"
        amend_marker = f"TAF AMD {station}"
        candidate = None
        for prod in sorted(
            self.store._products.values(), key=lambda p: p.timestamp, reverse=True
        ):
            if prod.product_type != "TAF":
                continue
            text = prod.raw_text
            if target_marker in text or amend_marker in text or f"\n{station} " in text:
                candidate = prod
                break
        if candidate is None:
            return None

        issued_hours_ago = max(
            0,
            int(
                (
                    now_utc_minutes()
                    - candidate.timestamp.hour * 60
                    - candidate.timestamp.minute
                )
                / 60
            ),
        )
        return encode_taf(station, candidate.raw_text, issued_hours_ago)

    def _build_warnings_near(self, loc: dict, query: str) -> bytes | None:
        """Build a 0x37 'warnings near location' summary.

        Pulls all currently-active warnings from extract_active_warnings(),
        filters to those that affect the requested location's zone, and packs
        them as a compact 0x37 reply with type/severity/expiry per entry.

        Works for both land zones (which the resolver knows about via
        zones.json) AND marine zones (PMZ###, GMZ###, etc. which aren't
        in the resolver but ARE valid UGC codes that pyIEM extracts from
        warning products). For marine zones we skip the polygon fallback
        and rely purely on UGC code matching.
        """
        # If the request was for a bare zone code, use it directly without
        # going through the resolver — that lets us serve marine zones
        # (PMZ172 etc.) which aren't in zones.json.
        zone: str = ""
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
        # Filter to warnings whose UGCs include our zone (or whose polygon
        # contains the zone's centroid as a fallback for land zones).
        nearby: list[dict] = []
        z_meta = resolver._zones.get(zone, {})
        z_lat = z_meta.get("la", 0.0)
        z_lon = z_meta.get("lo", 0.0)
        for w in all_warnings:
            ugcs = set(w.get("ugcs") or w.get("zones", []))
            in_zone = zone in ugcs
            if not in_zone and w.get("vertices"):
                # Polygon containment check (point-in-polygon for the zone centroid)
                from meshcore_weather.protocol.coverage import _point_in_polygon
                if _point_in_polygon(z_lat, z_lon, w["vertices"]):
                    in_zone = True
            if not in_zone:
                continue
            # Pick a representative zone for the per-entry zone reference
            entry_zone = zone if zone in ugcs else (sorted(ugcs)[0] if ugcs else "")
            expires_at = w.get("expires_at")
            expires_unix_min = (
                int(expires_at.timestamp() / 60) if expires_at else 0
            )
            nearby.append({
                "warning_type": w.get("warning_type", 0),
                "severity": w.get("severity", SEV_WARNING),
                "expires_unix_min": expires_unix_min,
                "zone": entry_zone if len(entry_zone) == 6 and entry_zone[2] == "Z" else "",
            })

        if not nearby:
            return None
        return pack_warnings_near(LOC_ZONE, zone, nearby)
