# iOS Developer Brief — MeshWX Weather Bot

Everything in this document describes what is **actually shipping in the bot today** on `main`. Anything not here is not yet wired up. If something is documented elsewhere as "planned" or "target state," treat it as non-existent for integration purposes.

The canonical wire format references are `docs/MeshWX_Protocol_v3.md` (legacy) and `docs/MeshWX_Protocol_v4_Design.md` in the same repo. The v4 client implementation guide is `docs/v4_client_guide.md`. This brief is the iOS-side summary with extra context about transport and flow.

---

## Quick orientation

- **Discovery channel** (`#meshwx-discover`): clients send a `0xF1` ping here; all nearby bots respond with `0xF0` beacons advertising their capabilities and data channel name.
- **Deployment channel** (e.g. `#aus-meshwx-v4`): the bot pushes binary weather data here. Your app listens passively on this channel. Clients also send data requests here.
- **DM path**: your app can send requests to the bot's public key as `text` DMs that start with the ASCII prefix `WXQ` followed by hex-encoded bytes. The bot decodes, processes, and then broadcasts the response on the deployment channel (not a DM reply — so all clients benefit from one request).
- **Every binary message on the deployment channel is COBS-encoded** before transmission because the MeshCore firmware's companion protocol truncates at null bytes. You MUST decode COBS before parsing the wire format.
- **v4 messages have a 6-byte frame header** (version `0x04` + msg_type + flags + group_total + uint16 sequence number). Auto-detect by checking if byte 0 == `0x04`.
- **Multi-byte integers are big-endian unless noted.** The few little-endian exceptions are called out inline.
- **Max message payload: 136 bytes** (one LoRa frame).

---

## Preload bundle (`client_data/`)

Everything in `client_data/` in this repo is meant to be bundled into your app at release time. Committed to git; grab it directly. Total ~9.9 MB.

| File | Size | Purpose |
|------|------|---------|
| `zones.json` | 347 KB | 4,029 NWS forecast zones: code → name, state, WFO, centroid lat/lon |
| `places.json` | 1.1 MB | 32,333 US Census places: name, state, lat/lon. Use for city-search autocomplete |
| `stations.json` | 181 KB | 2,237 METAR stations: ICAO → name, state, coordinates |
| `wfos.json` | 9 KB | 125 NWS Weather Forecast Offices: code → name, city |
| `state_index.json` | <1 KB | 51 US states/territories → 1-byte index. Used to decode `state_idx` in zone refs |
| `protocol.json` | 2 KB | Version marker + enum reference. Version 4 on `main` |
| `pfm_points.json` | 101 KB | 1,873 NWS PFM forecast points. Used for `LOC_PFM_POINT` city-forecast requests |
| `zones.geojson` | 8.1 MB | 4,047 simplified zone polygons (~1 km tolerance). Used to render `0x21` warnings as map shapes |
| `weather_dict.json` | 2 KB | Reserved for future dictionary compression — **not active in the wire format yet, ignore for now** |

### `pfm_points.json` format

Compact array form. **Array index IS the `pfm_point_id`** you send on the wire:

```json
{
  "version": 1,
  "points": [
    ["Aberdeen-Brown SD", "ABR", 45.45, -98.42, "SDZ006"],
    ...
    ["Austin Bergstrom-Travis TX", "EWX", 30.19, -97.67, "TXZ192"],
    ...
  ]
}
```

Each entry: `[name, wfo, lat, lon, zone]`. Ordering is deterministic (alphabetical by name, then WFO) so indices are stable across rebuilds.

### `zones.geojson` format

Standard GeoJSON `FeatureCollection`, one `Feature` per NWS public forecast zone. Each feature has a single `code` property:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"code": "TXZ192"},
      "geometry": {"type": "Polygon", "coordinates": [[...]]}
    }
  ]
}
```

When you decode a `0x21` warning message, look up each received zone code here and render the polygon. The wire format never carries zone geometry — this file is how the map view gets the actual shapes.

---

## Transport layer

### Discovery (finding bots)

1. Join `#meshwx-discover` and send a `0xF1` ping.
2. Each bot responds with a `0xF0` beacon after a random 1-5s delay (prevents air collisions).
3. Collect responses for ~10 seconds. Each beacon contains: protocol version, bot ID, capability flags (radar, warnings, forecasts, fire weather, nowcast, QPF), coverage center/radius, active warning count, and the bot's data channel name.
4. User picks a bot → client joins that bot's deployment channel.
5. Client can leave `#meshwx-discover` to free the channel slot.

See `docs/v4_client_guide.md` section 6 for the full beacon wire format.

### Deployment channel (receive)

1. Listen on the bot's deployment channel (e.g. `#aus-meshwx-v4`).
2. For every binary frame received: **decode COBS first**.
3. Check byte 0: if `0x04` → v4 frame (parse 6-byte header, then payload). If `>= 0x10` → legacy v3 message.
4. For v4 frames: extract `msg_type` from header byte 1, prepend it to the payload to get a v3-equivalent message, then dispatch by `msg_type`.
5. Track sequence numbers (header bytes 4-5) for gap detection and link quality.

COBS decode reference implementation (Swift sketch):

```swift
func cobsDecode(_ data: Data) -> Data {
    var out = Data()
    var i = 0
    while i < data.count {
        let code = Int(data[i])
        i += 1
        for _ in 0..<(code - 1) {
            guard i < data.count else { break }
            out.append(data[i])
            i += 1
        }
        if code < 0xFF && i < data.count {
            out.append(0x00)
        }
    }
    return out
}
```

### DM path (send data requests)

1. Build the binary `0x02` data request bytes using the format below.
2. Hex-encode those bytes as a lowercase/uppercase ASCII string.
3. Prepend `WXQ` to get a plain-text DM payload.
4. Send as a DM to the bot's public key.
5. Wait for the response to arrive on the deployment channel.

Example for an Austin forecast request:
```
bytes:    02 10 00 00 06 00 00 66    (8 bytes — see 0x02 format below)
hex:      0210000006000066
dm text:  WXQ0210000006000066
```

The DM path is intentionally text-only because MeshCore's DM transport is framed for text. The `WXQ` prefix lets the bot distinguish data requests from other DM traffic. The bot's response is NOT a DM reply — it's a broadcast on the deployment channel, so every listening client sees it.

### Rate limiting

The bot rate-limits responses at **5 minutes per `(data_type, location)`**. If user A and user B both request Austin's forecast within 30 seconds, the bot responds once to A and B gets the same answer from the same broadcast. If you send a request and nothing happens within a few seconds, another client probably already triggered the same data in the last 5 minutes — check your listener buffer.

---

## Message types (complete list of what's live)

### `0x01` — Refresh Request (reserved, legacy)

Legacy request type from v1. Still in the code for backward compatibility but **your iOS app should use `0x02` data requests instead** — they're more flexible.

### `0x02` — Data Request (iOS → bot, via DM)

The request you send when you want weather data for a specific location.

```
Offset  Size  Field
0       1     0x02
1       1     data_type (hi nibble) | flags (lo nibble)
2       2     client_newest (uint16 LE)   — minutes-since-midnight-UTC of
                                            the freshest data you already
                                            have cached, or 0 if none
4       N     location reference (type-tagged, 4-7 bytes depending on type)
```

**`data_type` (high nibble of byte 1)** — what you're asking for:

| Value | Name | Response message | Status |
|-------|------|------------------|--------|
| `0x0` | `DATA_WX` | `0x30` Observation | wired |
| `0x1` | `DATA_FORECAST` | `0x31` Forecast | wired |
| `0x2` | `DATA_OUTLOOK` | `0x32` Outlook | wired |
| `0x3` | `DATA_STORM_REPORTS` | `0x33` Storm Reports | wired |
| `0x4` | `DATA_RAIN_OBS` | `0x34` Rain Obs | wired |
| `0x5` | `DATA_METAR` | `0x30` Observation (same format as WX) | wired |
| `0x6` | `DATA_TAF` | `0x36` TAF | wired |
| `0x7` | `DATA_WARNINGS_NEAR` | `0x37` Warnings Near | wired |

All 8 data types are wired through the broadcaster. If the bot has data it will respond with the message type in the table. If it doesn't, it responds with a `0x03 NOT_AVAILABLE` (next section) so you can show an empty-state UI instead of spinning forever.

**`flags` (low nibble of byte 1)**: reserved for future use (verbose/force). Pass 0.

### `0x03` — Not Available (bot → client, broadcast)

The bot sends this when it **received and understood your request** but **can't produce the data you asked for**. **Your client MUST handle this message type** or it will miss an important signal.

```
Offset  Size  Field
0       1     0x03  MSG_NOT_AVAILABLE
1       1     data_type (hi nibble) | reason_code (lo nibble)
2       N     location reference — ECHOED from your original request so
              you can correlate the response with the pending request it
              corresponds to
```

Total size: **6-9 bytes** depending on location type.

**Reason codes** (low nibble of byte 1):

| Value | Name | What it means | UX suggestion |
|-------|------|---------------|---------------|
| `0x0` | `REASON_NO_DATA` | Bot parsed your request and looked for the source product but nothing is in its cache for that location | Show "No data available right now" and stop retrying |
| `0x1` | `REASON_LOCATION_UNRESOLVABLE` | The location you sent couldn't be resolved | Show "Unknown location". Don't retry — the request is malformed |
| `0x2` | `REASON_PRODUCT_UNSUPPORTED` | You sent a `data_type` the bot doesn't have a builder for | Check your protocol version against the bot's |
| `0x3` | `REASON_BOT_ERROR` | A builder raised an internal exception | Show "Temporary error, try again" |
| `0xF` | `REASON_UNKNOWN` | Fallback / catch-all | Same as bot_error |

### Location reference encoding (used inside `0x02` and as a field in many responses)

```
Byte 0: location_type
  0x01 = LOC_ZONE       (3 more bytes: state_idx + uint16 zone_num BE)      4 total
  0x02 = LOC_STATION    (4 more bytes: ICAO ASCII, e.g. "KAUS")             5 total
  0x03 = LOC_PLACE      (3 more bytes: uint24 index into places.json BE)    4 total
  0x04 = LOC_LATLON     (6 more bytes: int24 lat*10000 + int24 lon*10000 BE) 7 total
  0x05 = LOC_WFO        (3 more bytes: 3-letter WFO ASCII, e.g. "EWX")      4 total
  0x06 = LOC_PFM_POINT  (3 more bytes: uint24 index into pfm_points.json BE) 4 total
```

### `0x10` — Radar Grid (broadcast, legacy 16x16)

Legacy fixed-size radar. Still in the protocol but superseded by `0x11` compressed radar.

### `0x11` — Radar Compressed (broadcast, 32x32 or 64x64)

Compressed radar grid with sparse or RLE encoding. For 64x64 grids, the bot sends spatial quadrants with FEC (XOR parity) so any single missing quadrant can be recovered.

**v4 wire format:**
```
Offset  Size  Field
0       1     0x11
1       1     region_id (hi nibble) | chunk_seq (lo nibble)
2       1     grid_size (32 or 64)
3       4     timestamp_unix_min (uint32 BE — minutes since Unix epoch)
7       1     scale_km (uint8)
8       1     encoding (hi nibble: 0=sparse, 1=RLE) | total_chunks (lo nibble)
9+      N     encoded grid data
```

For 64x64, the bot sends an FEC group: 1 base layer (32x32 downsampled preview) + 4 spatial quadrants (NW/NE/SW/SE, each 32x32) + 1 XOR parity unit. See `docs/v4_client_guide.md` section 3 for FEC details.

### `0x12` — QPF Precipitation Grid (broadcast)

Quantitative precipitation forecast grid. Same wire format as `0x11` but byte 0 is `0x12` and 4-bit cell values represent precipitation amounts instead of reflectivity:

```
0x0 = none, 0x1 = trace-0.10", 0x2 = 0.10-0.25", 0x3 = 0.25-0.50",
0x4 = 0.50-0.75", 0x5 = 0.75-1.00", 0x6 = 1.00-1.50", 0x7 = 1.50-2.00",
0x8 = 2.00-2.50", 0x9 = 2.50-3.00", 0xA = 3.00-4.00", 0xB = 4.00-5.00",
0xC = 5.00-7.00", 0xD = 7.00-10.00", 0xE = 10.00"+
```

### `0x20` — Warning Polygon (broadcast)

Full warning with an explicit polygon. In v4, includes an onset time field.

```
Offset  Size  Field
0       1     0x20
1       1     warning_type (hi nibble) | severity (lo nibble)
2       4     expires_unix_min (uint32 BE) — absolute Unix minutes since epoch
6       4     onset_unix_min (uint32 BE, 0 = effective immediately)
10      1     vertex_count
11      6     first vertex: int24 BE lat/lon * 10000
17+     4     per remaining vertex: int16 BE dlat/dlon * 1000
remainder     headline, UTF-8, word-boundary truncated with "..." suffix
```

### `0x21` — Warning Zones (broadcast)

Zone-coded warning. In v4, includes an onset time field.

```
Offset  Size  Field
0       1     0x21
1       1     warning_type (hi nibble) | severity (lo nibble)
2       4     expires_unix_min (uint32 BE)
6       4     onset_unix_min (uint32 BE, 0 = effective immediately)
10      1     zone_count (max 30)
11      3     per zone (zone_count * 3 bytes):
                state_idx (uint8) + zone_num (uint16 BE)
remainder     headline, UTF-8, word-boundary truncated with "..." suffix
```

### `0x30` — Observation (broadcast — reply to `DATA_WX` / `DATA_METAR`)

Current conditions for one location.

```
Offset  Size  Field
0       1     0x30
1       N     location reference (type-tagged, 4-7 bytes)
N+1     2     timestamp_utc_min (uint16 LE — note: little-endian here)
N+3     1     temp_f (int8)
N+4     1     dewpoint_f (int8)
N+5     1     wind_dir (hi nibble, 0-15 compass) | sky_code (lo nibble)
N+6     1     wind_speed_mph (uint8)
N+7     1     wind_gust_mph (uint8, 0 = no gust)
N+8     1     visibility_mi (uint8)
N+9     1     pressure: (inHg - 29.00) * 100
N+10    1     feels_like_delta (int8, signed delta from temp_f)
```

**Sky code** (low nibble):
```
0x0 clear      0x4 overcast    0x8 rain     0xC mist
0x1 few        0x5 fog         0x9 snow     0xD squall
0x2 scattered  0x6 smoke       0xA tstorm   0xE sand
0x3 broken     0x7 haze        0xB drizzle  0xF other
```

**Wind direction** (high nibble): 16-point compass (0=N, 1=NNE, 2=NE, ..., 15=NNW).

### `0x31` — Forecast (broadcast — reply to `DATA_FORECAST`)

Multi-period forecast for one location. Data sourced from PFM (preferred), ZFP, or SFT (nationwide fallback).

```
Offset  Size  Field
0       1     0x31
1       N     location reference (type-tagged, 4-7 bytes)
N+1     1     issued_hours_ago (uint8)
N+2     1     period_count (uint8)
then    7     per period (period_count * 7 bytes):
                1 byte  period_id (day offset from first complete day)
                1 byte  high_f (int8, 127 = N/A)
                1 byte  low_f (int8, 127 = N/A)
                1 byte  sky_code
                1 byte  precip_pct (uint8, 0-100)
                1 byte  wind_dir (hi nibble) | wind_speed_5mph (lo nibble)
                1 byte  condition_flags (bitfield)
```

**`condition_flags`** (byte 7):
```
bit 0 (0x01): thunderstorms possible
bit 1 (0x02): frost possible
bit 2 (0x04): fog possible
bit 3 (0x08): high wind advisory
bit 4 (0x10): freezing rain/sleet
bit 5 (0x20): heavy rain
bit 6 (0x40): heavy snow
bit 7 (0x80): reserved
```

### `0x32` — Outlook (broadcast — reply to `DATA_OUTLOOK`)

1-7 day hazard outlook from the Hazardous Weather Outlook (HWO) product.

### `0x33` — Storm Reports (broadcast — reply to `DATA_STORM_REPORTS`)

Up to 8 confirmed Local Storm Reports (LSR) in one message.

### `0x34` — Rain Observations (broadcast — reply to `DATA_RAIN_OBS`)

Cities currently reporting rain.

### `0x36` — TAF (broadcast — reply to `DATA_TAF`)

Terminal aerodrome forecast for a station.

### `0x37` — Warnings Near Location (broadcast — reply to `DATA_WARNINGS_NEAR`)

Summary of active warnings near a requested location. Contains warning type, severity, expiry, and zone code per entry.

### `0x38` — Fire Weather Forecast (broadcast)

Per-zone fire weather data from the FWF product.

```
Offset  Size  Field
0       1     0x38
1       N     location (zone reference)
N+1     1     issued_hours_ago (uint8)
N+2     1     period_count (uint8, 1-7)

Per period (8 bytes):
  byte 0     : period_id
  byte 1     : max_temp_f (int8)
  byte 2     : min_rh_pct (uint8, 0-100)
  byte 3     : transport_wind (hi nibble: dir, lo nibble: speed in 5mph)
  byte 4     : mixing_height_500ft (uint8, multiply by 500 for feet AGL)
  byte 5     : haines_lightning (hi nibble: lightning risk, lo nibble: Haines index 2-6)
  byte 6     : cloud_cover
  byte 7     : weather_byte
```

### `0x3A` — Daily Climate Summary (broadcast)

Batched daily climate observations from the RTP product.

```
Offset  Size  Field
0       1     0x3A
1       1     city_count (uint8, 1-18)
2       1     report_day_offset (uint8, 0=today, 1=yesterday)

Per city (7 bytes):
  bytes 0-2  : place_id (uint24 BE)
  byte 3     : max_temp_f (int8, 127 = missing)
  byte 4     : min_temp_f (int8, 127 = missing)
  byte 5     : precip_hundredths (uint8, 0xFF=trace, 0xFE=missing)
  byte 6     : snow_tenths (uint8, 0xFF=trace, 0xFE=missing)
```

### `0x3C` — Nowcast / Short-Term Forecast (broadcast)

1-3 hour tactical forecast from the NOW product.

```
Offset  Size  Field
0       1     0x3C
1       N     location (typically LOC_WFO)
N+1     1     valid_hours (uint8, 1-3)
N+2     1     urgency_flags
                bit 0: has_thunder
                bit 1: has_flooding
                bit 2: has_winter
                bit 3: has_fire
                bit 4: has_wind
N+3+    ..    text payload (UTF-8, truncated to fit frame)
```

### `0xF0` — Discovery Beacon (bot → clients, on `#meshwx-discover`)

See Discovery section above and `docs/v4_client_guide.md` section 6 for full wire format.

### `0xF1` — Discovery Ping (client → bots, on `#meshwx-discover`)

Single-byte message that triggers all bots to respond with their `0xF0` beacon.

---

## Warning type + severity encoding (shared by `0x20`, `0x21`, `0x37`)

Byte 1 of every warning message packs two 4-bit fields:

**`warning_type` nibble**:

| Value | Name | Maps to NWS products |
|-------|------|----------------------|
| `0x1` | `WARN_TORNADO` | TOR |
| `0x2` | `WARN_SEVERE_TSTORM` | SVR, SVS |
| `0x3` | `WARN_FLASH_FLOOD` | FFW |
| `0x4` | `WARN_FLOOD` | FLW, FLS, FA |
| `0x5` | `WARN_WINTER_STORM` | WSW |
| `0x6` | `WARN_HIGH_WIND` | HWW, WI |
| `0x7` | `WARN_FIRE` | RFW, FWW |
| `0x8` | `WARN_MARINE` | MWW, MWS, SMW |
| `0x9` | `WARN_SPECIAL` | SPS (Special Weather Statement) |
| `0xF` | `WARN_OTHER` | anything else |

**`severity` nibble**: `0x1`=advisory, `0x2`=watch, `0x3`=warning, `0x4`=emergency

---

## City search → forecast flow (end-to-end)

```
User types "Austin"
      |
Autocomplete from places.json                        [client]
      |
User picks "Austin, TX"                              [client]
      |
Read lat/lon from places.json entry                  [client]
      |
Find nearest entry in pfm_points.json                [client]
  -> ["Austin Bergstrom-Travis TX", "EWX", 30.19,
     -97.67, "TXZ192"] at array index 102
      |
Build 0x02 request:
  data_type  = DATA_FORECAST (0x1)
  loc_type   = LOC_PFM_POINT (0x6)
  loc_id     = 102
      |
Hex-encode, prepend "WXQ", send as DM                [client]
  -> "WXQ0210000006000066"
      |
------------------------------------------------------
      |
Bot receives DM, strips "WXQ", hex-decodes           [bot]
      |
Builds 0x31 forecast with loc_type = LOC_PFM_POINT   [bot]
      |
Wraps in v4 frame, COBS-encodes, broadcasts
      |
------------------------------------------------------
      |
Every listening client receives the broadcast
COBS-decode -> v4 unwrap -> check 0x31 for your pfm_point_id
If matches -> update the Austin forecast in your cache
```

---

## What's NOT on the wire yet

Don't write client code against any of the following — they're planned but not implemented:

- **Full VTEC action codes** as a separate wire field (`NEW`/`CON`/`EXT`/`CAN`/`UPG`). The bot internally tracks them via pyIEM but the wire format doesn't carry them. `CAN`/`EXP` warnings are filtered out server-side.
- **Event Tracking Number (ETN)** as a separate field.
- **CAP urgency and certainty** fields.
- **County FIPS codes in `0x21`** — only Z-zone codes are emitted.
- **H-VTEC fields** for flood products (flood severity, immediate cause, NWSLI river ID).
- **Dictionary text compression** using `weather_dict.json`. The file ships but no encoder emits the escape bytes.
- **68-entry VTEC phenomenon table.** Still uses the coarse 4-bit `warning_type` nibble.
- **3-hourly forecast resolution.** `0x31` is 7 daily periods max.
- **Multi-bot roaming** (auto-switching bots based on signal/location). The discovery mechanism supports it but the client-side logic isn't built.

---

## Text-command DM path (separate, unchanged)

The bot still accepts plain-text commands via DM (e.g. `weather austin tx`, `wx KAUS`, `forecast Austin TX`) and replies with plain-text DMs. This is the legacy text-bot interface. You can use it for debugging or as a fallback.

---

## Debug checklist

1. **Are you COBS-decoding before parsing?** Every binary broadcast is COBS-encoded.
2. **Are you checking for v4 frames?** Byte 0 == `0x04` means v4 frame with a 6-byte header. Byte 0 >= `0x10` means legacy v3.
3. **Are you reading `expires_unix_min` as uint32 BE?** Not LE, not relative minutes.
4. **v4 warning onset field**: `0x20` and `0x21` now have 4 extra bytes (onset_unix_min) between expiry and vertex/zone data compared to v3.
5. **v4 radar timestamp**: `0x11` timestamp is now uint32 (4 bytes, Unix minutes) instead of uint16 (2 bytes, minutes since midnight). All offsets after byte 2 shift by +2 compared to v3.
6. **For `DATA_FORECAST` responses, is the `0x31`'s location type matching your request?** The bot echoes your requested loc type.
7. **Rate limit**: if nothing arrives, check whether another client requested the same `(data_type, location)` within the last 5 minutes.

---

## References

- **v4 client guide**: `docs/v4_client_guide.md` in this repo
- **v4 design doc**: `docs/MeshWX_Protocol_v4_Design.md` in this repo
- **Legacy v3 wire format**: `docs/MeshWX_Protocol_v3.md` in this repo
- **Source of truth (Python)**: `meshcore_weather/protocol/meshwx.py`
- **pyIEM (reference NWS text parser)**: https://github.com/akrherz/pyIEM
