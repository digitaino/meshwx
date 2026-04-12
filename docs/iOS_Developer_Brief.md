# iOS Developer Brief — MeshWX Weather Bot

Everything in this document describes what is **actually shipping in the bot today** (commit `9d08fde` or later on `main`). Anything not here is not yet wired up. If something is documented elsewhere as "planned" or "target state," treat it as non-existent for integration purposes.

The canonical wire format reference is `docs/MeshWX_Protocol_v3.md` in the same repo. This brief is the iOS-side summary with extra context about transport and flow.

---

## Quick orientation

- **Broadcast channel** (`#wx-broadcast`): the bot pushes binary weather data here. Your app listens passively on this channel.
- **DM path**: your app sends requests to the bot's public key as `text` DMs that start with the ASCII prefix `WXQ` followed by hex-encoded bytes. The bot decodes, processes, and then broadcasts the response on the broadcast channel (not a DM reply — so all clients benefit from one request).
- **Every binary message on the broadcast channel is COBS-encoded** before transmission because the MeshCore firmware's companion protocol truncates at null bytes. You MUST decode COBS before parsing the wire format. See "Transport" section below.
- **Multi-byte integers are big-endian unless noted.** The few little-endian exceptions are called out inline.
- **Max message payload: 136 bytes** (one LoRa frame).
- **`docs/MeshWX_Protocol_v3.md`** is the authoritative wire-format reference.

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

### Broadcast channel (receive)

1. Listen on the configured broadcast channel (default: `#wx-broadcast`).
2. For every binary frame received: **decode COBS first**, then parse the resulting bytes as one of the message types below.
3. Dispatch by the first byte (`msg_type`).

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
5. Wait for the response to arrive on `#wx-broadcast` (same channel where broadcasts come from).

Example for an Austin forecast request:
```
bytes:    02 10 00 00 06 00 00 66    (8 bytes — see 0x02 format below)
hex:      0210000006000066
dm text:  WXQ0210000006000066
```

The DM path is intentionally text-only because MeshCore's DM transport is framed for text. The `WXQ` prefix lets the bot distinguish data requests from other DM traffic. The bot's response is NOT a DM reply — it's a broadcast on `#wx-broadcast`, so every listening client sees it.

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
| `0x0` | `DATA_WX` | `0x30` Observation | ✅ wired |
| `0x1` | `DATA_FORECAST` | `0x31` Forecast | ✅ wired |
| `0x2` | `DATA_OUTLOOK` | `0x32` Outlook | ✅ wired |
| `0x3` | `DATA_STORM_REPORTS` | `0x33` Storm Reports | ✅ wired |
| `0x4` | `DATA_RAIN_OBS` | `0x34` Rain Obs | ✅ wired |
| `0x5` | `DATA_METAR` | `0x30` Observation (same format as WX) | ✅ wired |
| `0x6` | `DATA_TAF` | `0x36` TAF | ✅ wired |
| `0x7` | `DATA_WARNINGS_NEAR` | `0x37` Warnings Near | ✅ wired |

All 8 data types are now wired through the broadcaster. If the bot has data it will respond with the message type in the table. If it doesn't, it responds with a `0x03 NOT_AVAILABLE` (next section) so you can show an empty-state UI instead of spinning forever.

**`flags` (low nibble of byte 1)**: reserved for future use (verbose/force). Pass 0.

### `0x03` — Not Available (bot → client, broadcast)

The bot sends this when it **received and understood your request** but **can't produce the data you asked for**. Previously these cases were silent drops, which made far-away requests look like "spinning forever" even when the bot was actively rejecting them. **Your client MUST handle this message type** or it will miss an important signal.

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
| `0x0` | `REASON_NO_DATA` | Bot parsed your request and looked for the source product (METAR, ZFP, PFM, LSR, etc.) but nothing is currently in its cache for that location | Show "No data available right now" and stop retrying. The bot's EMWIN cache refreshes every 2 minutes — try again in a few minutes if the data is expected to arrive. |
| `0x1` | `REASON_LOCATION_UNRESOLVABLE` | The location you sent couldn't be resolved: an out-of-range `pfm_point_id`, an unknown zone code, a bad station ICAO, etc. | Show "Unknown location". Don't retry — the request is malformed, not transient. |
| `0x2` | `REASON_PRODUCT_UNSUPPORTED` | You sent a `data_type` the bot doesn't have a builder for (should be unreachable now that all 8 types are wired, but reserved for future protocol additions) | Check your protocol version against the bot's |
| `0x3` | `REASON_BOT_ERROR` | A builder raised an internal exception | Show "Temporary error, try again". Log and file a bug |
| `0xF` | `REASON_UNKNOWN` | Fallback / catch-all | Same as bot_error |

**Correlating `0x03` with your pending requests**: the `data_type` + `location` fields in the response match what you sent in your `0x02`. Your client should key its pending-request table by `(data_type, location)` and look up the matching entry when a `0x03` arrives.

**Cached + double-transmitted** like any other v2 response. Retrying a request that recently got `NOT_AVAILABLE` will receive a rebroadcast from the cache — no re-evaluation on the bot side. So if your first receipt of the `0x03` was lost in the mesh, a retry gives you another shot at receiving it.

**Decoder sketch** (Swift):

```swift
struct NotAvailable {
    let dataType: UInt8       // which data_type the bot couldn't serve
    let reason: UInt8          // REASON_* code
    let location: Location     // echoed from your request

    static func decode(_ bytes: Data) -> NotAvailable? {
        guard bytes.count >= 3, bytes[0] == 0x03 else { return nil }
        let dataType = (bytes[1] >> 4) & 0x0F
        let reason = bytes[1] & 0x0F
        guard let location = Location.decode(bytes, offset: 2) else { return nil }
        return NotAvailable(dataType: dataType, reason: reason, location: location)
    }
}
```

**`client_newest` (bytes 2-3, uint16 LE)**: lets the bot skip sending data you already have. The bot may not check this yet; safe to send 0 or the minutes-since-midnight-UTC of your newest cached entry.

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

For the wired data types, use these locations:
- **`DATA_WX` / `DATA_METAR`**: `LOC_STATION` (preferred) or `LOC_ZONE`
- **`DATA_FORECAST`**: `LOC_PFM_POINT` (preferred for city forecasts) or `LOC_ZONE`

### `0x10` — Radar Grid (broadcast)

Periodic radar broadcast from the bot. 133 bytes fixed length.

```
Offset  Size  Field
0       1     0x10
1       1     region_id (hi nibble) | frame_seq (lo nibble)
2       2     timestamp_utc_min (uint16 BE) — minutes since midnight UTC
4       1     scale_km — grid spacing in km
5       128   16×16 grid, 4 bits per cell (two cells per byte)
```

Each 4-bit cell is a dBZ reflectivity bin (0 = no echo, 0xE = heavy). The 10 regions are fixed in `meshcore_weather/protocol/meshwx.py` under `REGIONS` (Northeast, Southeast, Southern, etc.). For the bot's TX coverage you'll see regions `0x3` Southern, `0x4` Central, `0x5` Mountain in rotation.

### `0x20` — Warning Polygon (broadcast)

Full warning with an explicit polygon. Used when the warning has a storm-specific shape (tornado, severe thunderstorm, flash flood) that doesn't match a zone boundary.

```
Offset  Size  Field
0       1     0x20
1       1     warning_type (hi nibble) | severity (lo nibble)
2       4     expires_unix_min (uint32 BE) — absolute Unix minutes since epoch
6       1     vertex_count
7       6     first vertex:
                int24 BE lat  (× 10000, ~11 m precision)
                int24 BE lon  (× 10000)
then    4     per remaining vertex: (vertex_count - 1) × 4 bytes
                int16 BE dlat (× 1000, ~110 m precision, ±32.767° from lat0)
                int16 BE dlon (× 1000)
remainder     headline, UTF-8, word-boundary truncated with "..." suffix
```

Convert `expires_unix_min` to a display time:
```swift
let expiresAt = Date(timeIntervalSince1970: TimeInterval(expiresUnixMin) * 60)
let minutesLeft = Int(expiresAt.timeIntervalSinceNow / 60)
if minutesLeft <= 0 {
    // expired — drop from display, don't show a countdown
}
```

### `0x21` — Warning Zones (broadcast)

Preferred form for warnings with NWS zone codes (winter storm, wind advisory, heat, marine, etc.). Much smaller than polygon encoding; the iOS client renders the shape by looking up each zone code in `zones.geojson`.

```
Offset  Size  Field
0       1     0x21
1       1     warning_type (hi nibble) | severity (lo nibble)
2       4     expires_unix_min (uint32 BE)
6       1     zone_count (max 30)
7       3     per zone (zone_count × 3 bytes):
                state_idx (uint8, index into state_index.json)
                uint16 BE zone_num  (e.g. 192 for TXZ192)
remainder     headline, UTF-8, word-boundary truncated with "..." suffix
```

Rebuild the canonical zone code in your decoder: `"\(state)Z\(zoneNum, width: 3, leadingZeros: true)"` → `"TXZ192"`. Look it up in `zones.geojson` for rendering.

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
N+9     1     pressure: (inHg - 29.00) * 100 → byte range ≈ 29.00 to 31.55
N+10    1     feels_like_delta (int8, signed delta from temp_f)
```

**Sky code** (low nibble of byte N+5):
```
0x0 clear      0x4 overcast    0x8 rain     0xC mist
0x1 few        0x5 fog         0x9 snow     0xD squall
0x2 scattered  0x6 smoke       0xA tstorm   0xE sand
0x3 broken     0x7 haze        0xB drizzle  0xF other
```

**Wind direction** (high nibble of byte N+5): index into the 16-point compass:
```
0:N  1:NNE  2:NE  3:ENE  4:E  5:ESE  6:SE  7:SSE
8:S  9:SSW  10:SW  11:WSW  12:W  13:WNW  14:NW  15:NNW
```

**For `DATA_METAR` requests**, the bot currently responds with this same `0x30` format (METAR-derived data goes through the same encoder). There is no distinct `0x35` on the wire today even though the message type is defined.

### `0x31` — Forecast (broadcast — reply to `DATA_FORECAST`)

Multi-period forecast for one location. The bot echoes your original `location_type` in the response, so a `LOC_PFM_POINT` request gets a `LOC_PFM_POINT` response — you can correlate broadcasts with outstanding requests by matching the `pfm_point_id`.

```
Offset  Size  Field
0       1     0x31
1       N     location reference (type-tagged, 4-7 bytes)
N+1     1     issued_hours_ago (uint8) — hours since the underlying NWS
                                         product was issued
N+2     1     period_count (uint8)
then    7     per period (period_count × 7 bytes):
                1 byte  period_id  (see table below)
                1 byte  high_f (int8, 127 = N/A)
                1 byte  low_f (int8, 127 = N/A)
                1 byte  sky_code (same table as 0x30)
                1 byte  precip_pct (uint8, 0-100)
                1 byte  wind_dir (hi nibble) | wind_speed_5mph (lo nibble)
                1 byte  condition_flags (bitfield, see below)
```

**`period_id`** is the **day offset (0, 1, 2, ...) from the first complete day in the underlying NWS Point Forecast Matrix (PFM) product** the bot used to build the forecast.

- `period_id = 0` → the first full day in the forecast (NOT necessarily "today" — see below)
- `period_id = 1` → the next day
- ... up to `period_id = 6` for 7 days out

Each period represents a **full calendar day in the issuing WFO's local timezone**, with `high_f` being the daytime max and `low_f` being the nighttime min for that local day.

**Why "first complete day" instead of "always today"**: PFMs are issued ~2x/day at varying times. A PFM issued at 1 PM CDT only has Friday afternoon data left in it — that's not enough hours to compute a meaningful daily high/low for Friday, so the bot drops Friday and starts the forecast at Saturday (`period_id = 0` = Saturday). If the same PFM had been issued at 6 AM CDT, Friday would have a full day of remaining forecast data and you'd see Friday as `period_id = 0`. The bot's downsampler enforces a minimum-data filter (≥4 PFM time slots per day) to avoid emitting misleading partial-day aggregates.

**For your UI**:
- Don't hardcode `period_id == 1 → "Today"` style mappings — the values are sequential day offsets, not named-period codes.
- Compute display labels from `(broadcastReceivedDate + period_id days)` if you want to show "Mon / Tue / Wed". This is approximate (off by one possible at midnight boundaries) but good enough for a 7-day strip.
- Or display as "Day 1 / Day 2 / Day 3" if you want to play it safe.
- Always display periods in `period_id` ascending order. They're emitted in order today, but don't assume that.

**`wind_speed_5mph`** (low nibble of byte 6): wind speed in units of 5 mph. `3` = 15 mph, `5` = 25 mph, etc. 0 means calm.

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

**Data source**: the bot now sources `0x31` data from the canonical NWS Point Forecast Matrix (PFM) — a structured fixed-column table updated by every WFO ~2x/day for ~1,800 forecast points nationwide. Hard numeric values for temp / dewpoint / RH / wind / sky cover / 12hr PoP / 12hr QPF / rain / tstm / obstruction. Highs and lows are real readings from the PFM Temp row, downsampled to daily aggregates.

**Defensive note**: if no PFM product is available for a zone (rare — < 1% of US zones have no associated PFM point), the bot falls back to the legacy ZFP narrative-text regex parser, which can produce `high_f = None` (decoded as the int8 sentinel `127`) for many zones because the regex patterns are fragile. Keep your `None`/`127` handling defensive — show "—" or hide the field rather than crashing.

**Field accuracy summary** (after PFM landed):

| Field | Source | Notes |
|---|---|---|
| `high_f`, `low_f` | PFM Temp row, daytime max + nighttime min | Real numbers, not regex-extracted |
| `sky_code` | PFM Cloud row + rain/tstm/obvis precedence | Thunderstorm > rain > snow > fog > cloud cover |
| `precip_pct` | PFM `PoP 12hr` row, max for the day | NWS-authoritative percentage |
| `wind_dir_nibble` | PFM Wind dir, mode of daytime hours | 16-point compass index |
| `wind_speed_5mph` | PFM Wind spd, average of daytime hours, ÷5 | Capped 0-15 |
| `condition_flags` | OR of per-slot flag derivations | tstm/fog/heavy_rain/frost/high_wind/freezing_rain bits |

### `0x32`–`0x34`, `0x36`, `0x37` — Defined but not broadcast by the bot yet

- `0x32 Outlook` — has pack/unpack functions, no broadcaster wiring
- `0x33 Storm Reports` — has pack/unpack functions, no broadcaster wiring
- `0x34 Rain Obs` — has pack/unpack functions, no broadcaster wiring
- `0x36 TAF` — reserved, not implemented
- `0x37 Warnings Near Location` — has pack/unpack functions, no broadcaster wiring

You can read the wire format for these in `docs/MeshWX_Protocol_v3.md` but do not expect to see them on the broadcast channel yet.

### `0x40` — Text Chunk (reserved)

Planned for fallback text content (AFD narrative, etc.). Not yet broadcast. Reserved in the protocol.

---

## Warning type + severity encoding (shared by `0x20`, `0x21`)

Byte 1 of every warning message packs two 4-bit fields:

```
bits 7-4 (high nibble): warning_type
bits 3-0 (low nibble):  severity
```

**`warning_type` nibble** (coarse category):

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

**`severity` nibble**:
```
0x1 = SEV_ADVISORY
0x2 = SEV_WATCH
0x3 = SEV_WARNING
0x4 = SEV_EMERGENCY
```

Use these to pick display color/icon and whether to trigger a notification.

---

## City search → forecast flow (end-to-end)

This is the flow that consumes `pfm_points.json` and is the primary use case for `LOC_PFM_POINT`:

```
User types "Austin"
      ↓
Autocomplete from places.json                        [client]
      ↓
User picks "Austin, TX"                              [client]
      ↓
Read lat/lon from places.json entry                  [client]
      ↓
Find nearest entry in pfm_points.json                [client]
  → ["Austin Bergstrom-Travis TX", "EWX", 30.19,
     -97.67, "TXZ192"] at array index 102
      ↓
Build 0x02 request:
  data_type  = DATA_FORECAST (0x1)
  loc_type   = LOC_PFM_POINT (0x6)
  loc_id     = 102
      ↓
Hex-encode, prepend "WXQ", send as DM                [client]
  → "WXQ0210000006000066"   (19 bytes of text on the DM path)
      ↓
──────────────────────────────────────────────────────
      ↓
Bot receives DM, strips "WXQ", hex-decodes           [bot]
      ↓
Unpacks 0x02 data request                            [bot]
      ↓
Reads its own pfm_points.json[102]                   [bot]
  → ("Austin Bergstrom-Travis TX", "EWX", "TXZ192")
      ↓
Looks up ZFP product for EWX/TX zone 192             [bot]
      ↓
Encodes 0x31 forecast with                           [bot]
  loc_type = LOC_PFM_POINT
  loc_id   = 102   ← echoes your original request
      ↓
COBS-encodes and broadcasts on #wx-broadcast
      ↓
──────────────────────────────────────────────────────
      ↓
Every listening client receives the broadcast
COBS-decode, check the 0x31 for your pfm_point_id
If matches → update the Austin forecast in your cache
```

**Rate limit**: 5 minutes per `(DATA_FORECAST, pfm_point_id=102)`. If another client requested Austin within the last 5 minutes, the bot won't re-broadcast — you should look in your cache for the recent broadcast instead.

---

## What's NOT on the wire yet (be honest with yourself about this)

Don't write client code against any of the following — they're planned but not implemented:

- **VTEC action codes** (`NEW`/`CON`/`EXT`/`CAN`/`UPG` etc. as a separate wire field). The bot internally tracks them via pyIEM but the wire format doesn't carry them. Today `CAN`/`EXP` warnings are simply filtered out server-side (not broadcast), so you won't see them. `NEW`/`CON` warnings look identical on the wire.
- **Event Tracking Number (ETN)** as a separate field. For deduplication you currently only have `(warning_type, severity, expires_unix_min, first_vertex_or_zone)` to work with.
- **Issuing office (WFO)** as a separate field.
- **CAP urgency and certainty** fields.
- **County FIPS codes in `0x21`** (`TXC###`). The bot internally extracts them but `pack_warning_zones` only emits zone `Z` codes. Warnings that only have C-codes in their UGC fall back to `0x20` polygon encoding.
- **H-VTEC fields** for flood products (flood severity, immediate cause, NWSLI river ID, stage forecast).
- **Dictionary text compression** using `weather_dict.json`. The file ships but no encoder emits the escape bytes.
- **Proactive warning push** (immediate broadcast when a new warning is issued for the bot's coverage area). Currently warnings only go out on the periodic broadcast cycle.
- **68-entry VTEC phenomenon table.** The bot still uses the coarse 4-bit `warning_type` nibble; no protocol-wide NWS-canonical phenomenon indexing.
- **3-hourly forecast resolution.** `0x31` is 7 daily periods max.
- **`0x32 Outlook`, `0x33 Storm Reports`, `0x34 Rain Obs`, `0x36 TAF`, `0x37 Warnings Near`** broadcasts. Wire formats are defined but no broadcaster emits them.

These will come in future commits. Watch the repo CHANGELOG or the commit log for `0x3X` wire changes or new `LOC_*` types.

---

## Text-command DM path (separate, unchanged)

Completely separate from everything above: the bot still accepts plain-text commands via DM (e.g. `weather austin tx`, `wx KAUS`, `forecast Austin TX`) and replies with plain-text DMs. This is the legacy text-bot interface and is **not affected by any v3 wire format change**. You can use it for debugging or as a fallback, but it's not the primary integration path.

---

## Debug checklist

If the iOS app is behaving unexpectedly, start here:

1. **Are you COBS-decoding before parsing?** Every binary broadcast on `#wx-broadcast` is COBS-encoded. If the first byte of your "raw" message isn't one of the `0x0X`/`0x1X`/`0x2X`/`0x3X` values above, you probably forgot.
2. **Are you reading `expires_unix_min` as uint32 BE?** Not LE, not relative minutes. If you're seeing a comically large "minutes remaining" value like 363 hours, you're probably still decoding as v2.
3. **Is your polygon vertex decoding respecting the 3-byte int24 first vertex and 2-byte int16 deltas?** The first vertex is 6 bytes total. Subsequent vertices are 4 bytes total each.
4. **For `DATA_FORECAST` responses, is the `0x31`'s location type matching your request?** If you sent `LOC_PFM_POINT = 102` and the response has `LOC_ZONE = TXZ192`, that's a bug in the bot — tell us. Current code should always echo the requested loc type back.
5. **Is the bot actually broadcasting what you expect?** You can tail the bot's logs via the portal at `http://bot-host:8080/status` if it's running locally, or use the text-command DM path as a sanity check.
6. **Rate limit**: if nothing arrives, check whether another client requested the same `(data_type, location)` within the last 5 minutes — the bot may have already broadcast the answer.

---

## References

- **Canonical wire format**: `docs/MeshWX_Protocol_v3.md` in this repo
- **Source of truth (Python)**: `meshcore_weather/protocol/meshwx.py`
- **VTEC specification**: NWSI 10-1703 — https://www.nws.noaa.gov/directives/sym/pd01017003curr.pdf
- **UGC specification**: NWSI 10-1702 — https://www.weather.gov/media/directives/010_pdfs_archived/pd01017002b.pdf
- **pyIEM (reference NWS text parser)**: https://github.com/akrherz/pyIEM
- **VTEC browser (for looking up real warnings)**: https://mesonet.agron.iastate.edu/vtec/
- **CAP v1.2 (alert severity reference)**: https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html
