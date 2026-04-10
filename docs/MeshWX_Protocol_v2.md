# MeshWX Protocol v2 — Data Message Extensions

## Context

v1 of the protocol (shipped) defines three binary message types:
- `0x01` Refresh request (client → bot via DM)
- `0x10` Radar grid (16x16 reflectivity, broadcast)
- `0x20` Warning polygon (variable length, broadcast)

v2 adds data messages that replicate the bot's text commands (`wx`, `forecast`, `outlook`, etc.) in compact binary form so the iOS app and local web client can request and display weather data without parsing human text. The goal is **minimum airtime per useful byte**.

## Design principles

1. **Preload everything static on the client.** Client apps bundle NWS zones, places, and stations (~2 MB total) so broadcasts reference them by ID, not by name.
2. **Structured over freeform.** Send temperature/wind/sky codes, let the client format the text. A forecast period drops from ~200 bytes of text to ~7 bytes of structured fields.
3. **Dictionary-compressed text** for fields we can't structure (warning headlines, AFD snippets). Bundle the dictionary with clients.
4. **One message = one LoRa frame.** 136-byte max payload. If data is larger, split into chunked messages with sequence numbers.
5. **Back-compat.** Existing `0x10`, `0x20`, `0x01` unchanged. New types occupy the `0x30-0x40` range per the v1 spec's reserved bytes.

## Preloaded client data (not transmitted)

All MeshWX-aware clients ship with a `client_data/` bundle generated once from the bot's `meshcore_weather/geodata/` files:

| File | Size | Content | Access pattern |
|------|------|---------|----------------|
| `zones.json` | ~300 KB | 4,029 NWS zones: code → name, state, WFO, centroid lat/lon | Lookup by zone code |
| `places.json` | ~1.5 MB | 32,333 Census places: name, state, lat/lon | Index by place_id (array position) |
| `stations.json` | ~150 KB | 2,237 METAR stations: ICAO → name, state, lat/lon | Lookup by 4-letter ICAO |
| `wfos.json` | ~5 KB | ~120 WFO codes → office name + location | Lookup by 3-letter WFO |
| `zones.geojson` | ~3-5 MB | Simplified zone polygons | Draw warning areas |
| `weather_dict.json` | ~2 KB | Top-128 compression phrases | Decode text fields |

Total: ~5-7 MB preloaded. This is the cost of client apps — shipped once per release, **never transmitted over the mesh**.

## Location reference encoding

Every data message needs to reference a location. We use a 1-byte type tag + variable-length ID:

```
Byte 0: location_type
  0x01 = zone code (3 bytes: state_idx + zone_num uint16 LE)
  0x02 = station (4 bytes: ICAO ASCII)
  0x03 = place_id (3 bytes: uint24 index into preloaded places.json)
  0x04 = lat/lon (6 bytes: 2x int24 * 10000, as per warning polygon first vertex)
  0x05 = WFO code (3 bytes: ASCII)
```

For the vast majority of queries, zone (4 bytes total) or place_id (4 bytes total) will be smallest.

State encoding for zone codes: we use a fixed 1-byte index mapping. Bundled in client:
```
0x00 = AL, 0x01 = AK, 0x02 = AZ, ... 0x31 = DC, 0x32 = PR, 0x33 = GU, ...
```

## Message types

### `0x02` — Data Request (client → bot via DM)

Client asks the bot for specific weather data. Sent via DM for ACK reliability.

```
Offset  Size  Field
0       1     0x02
1       1     data_type (high nibble) | request_flags (low nibble)
                data_type: 0=wx, 1=forecast, 2=outlook, 3=storm_reports,
                           4=rain_obs, 5=metar, 6=taf, 7=warnings_near
                flags bit 0: verbose (prefer text fallback)
                flags bit 1: force (ignore cooldown)
2       2     client_newest (uint16 LE): minutes since midnight UTC of cached data
4       1     location_type
5       N     location_id (per encoding above)
```

Total: 7-11 bytes. Bot broadcasts the response on `#wx-broadcast` (so other listeners benefit).

### `0x30` — Observation (wx reply)

Compact current conditions for one location. Replicates the `wx <city>` command.

```
Offset  Size  Field
0       1     0x30
1       1     location_type
2       N     location_id
N+2     2     timestamp (uint16 LE): minutes since midnight UTC
N+4     1     temp_f (int8): temperature in °F
N+5     1     dewpoint_f (int8)
N+6     1     wind_dir (high nibble: 0-15 compass) | sky_code (low nibble)
              sky_code: 0=clear, 1=few, 2=scattered, 3=broken, 4=overcast,
                        5=fog, 6=smoke, 7=haze, 8=rain, 9=snow,
                        A=tstorm, B=drizzle, C=mist, D=squall, E=sand, F=other
N+7     1     wind_speed_mph (uint8)
N+8     1     wind_gust_mph (uint8, 0 = none)
N+9     1     visibility_mi (uint8)
N+10    1     pressure (uint8): (inHg - 29.00) * 100 → 29.00 to 31.55
N+11    1     feels_like (int8, relative to temp_f, signed delta)
```

Total: 12 + location_id bytes. For a zone location: **16 bytes**. For lat/lon: **19 bytes**. Compare to ~80 bytes of text for `wx Austin TX`.

### `0x31` — Forecast (forecast reply)

Multi-period forecast for a location. Fits 5-7 periods in one message.

```
Offset  Size  Field
0       1     0x31
1       1     location_type
2       N     location_id
N+2     1     issued_offset (uint8): hours ago the forecast was issued
N+3     1     period_count (uint8)

Per period (repeating):
  +0    1     period_id (uint8)
                0=tonight, 1=today, 2=tomorrow, 3=tomorrow night, 4-13 = next days,
                14=this afternoon, 15=this evening, 16=late tonight
  +1    1     high_f (int8, 127 = N/A)
  +2    1     low_f (int8, 127 = N/A)
  +3    1     sky_code (same as 0x30)
  +4    1     precip_pct (uint8, 0-100)
  +5    1     wind_dir (nibble) | wind_speed (nibble * 5 mph)
  +6    1     condition_flags
              bit 0: thunderstorms possible
              bit 1: frost possible
              bit 2: fog possible
              bit 3: high wind advisory
              bit 4: freezing rain/sleet
              bit 5: heavy rain
              bit 6: heavy snow
              bit 7: reserved
```

Per-period size: 7 bytes. For a 7-day forecast: 49 bytes + 6 byte header + 4 byte location = **59 bytes**. Text equivalent: ~500-1000 bytes.

### `0x32` — Outlook (HWO reply)

1-7 day hazard outlook. Replicates the `outlook` command.

```
Offset  Size  Field
0       1     0x32
1       1     location_type
2       N     location_id
N+2     2     issued_time (uint16 LE): minutes since midnight UTC
N+4     1     day_count (uint8): number of day entries

Per day (repeating):
  +0    1     day_offset (uint8): 1-7 (day 1 = today)
  +1    1     hazard_count (uint8, 0-15 typical)

  Per hazard (repeating):
    +0  1     hazard_type
                0=thunderstorm, 1=severe thunder, 2=tornado, 3=flood, 4=flash flood,
                5=excessive heat, 6=winter storm, 7=blizzard, 8=ice, 9=high wind,
                A=fire weather, B=dense fog, C=rip current, D=hurricane, E=marine,
                F=other
    +1  1     risk_level (uint8): 0=none, 1=slight, 2=limited, 3=enhanced,
                                  4=moderate, 5=high, 6=extreme
```

Per hazard: 2 bytes. Per day: 2 + 2*hazards. 7-day outlook with ~2 hazards/day: ~32 bytes + header + location = **42 bytes**.

### `0x33` — Storm Reports (LSR reply)

Up to 8 confirmed storm reports in one message. Replicates the `storm` command.

```
Offset  Size  Field
0       1     0x33
1       1     location_type (for the area these reports are from)
2       N     location_id
N+2     1     report_count (uint8)

Per report (repeating, 7 bytes each):
  +0    1     event_type
                0=tornado, 1=funnel, 2=hail, 3=wind damage, 4=non-tstm wind,
                5=tstm wind gust, 6=flood, 7=flash flood, 8=heavy rain,
                9=snow, A=ice, B=lightning damage, C=debris flow, D=other
  +1    1     magnitude (event-specific: hail size * 4 for inches, mph/10, etc.)
  +2    2     minutes_ago (uint16 LE)
  +4    3     place_id (uint24 index into preloaded places.json,
                       the nearest known place to the report)
```

Per report: 7 bytes. 8 reports in one message: 56 bytes + header + location = **66 bytes**.

### `0x34` — Rain observations (rain reply)

Cities currently reporting rain. Replicates the `rain` command.

```
Offset  Size  Field
0       1     0x34
1       1     location_type (region this covers)
2       N     location_id
N+2     2     timestamp (uint16 LE)
N+4     1     city_count (uint8)

Per city (5 bytes):
  +0    3     place_id (uint24 index)
  +3    1     rain_type (uint8)
                0=light rain, 1=rain, 2=heavy rain, 3=shower, 4=tstorm,
                5=drizzle, 6=snow, 7=freezing rain, ...
  +4    1     temp_f (int8)
```

Per city: 5 bytes. 20 rainy cities: 100 bytes + 7 byte header = fits in one message.

### `0x35` — METAR (raw aviation)

Compact METAR for a single station.

```
Offset  Size  Field
0       1     0x35
1       4     station_icao (4 bytes ASCII)
5       2     time (uint16 LE, minutes since midnight UTC)
7+       same fields as 0x30 observation (temp, wind, sky, etc.)
```

### `0x36` — TAF (terminal aerodrome forecast)

Structured forecast for a station. Like 0x31 but with TAF-specific fields (ceiling in ft, visibility in SM, specific wx codes).

### `0x37` — Warnings near location

Response to a "warnings near me" query. Contains a list of warning references (type, severity, zone code) that the client looks up in its warning cache.

```
Offset  Size  Field
0       1     0x37
1       1     location_type
2       N     location_id
N+2     1     warning_count (uint8)

Per warning (5 bytes):
  +0    1     type (high nibble) | severity (low nibble)
  +1    2     expiry_minutes (uint16 LE)
  +3    2     headline_hash (uint16) — client uses this to look up a
              previously-received full warning polygon (0x20)
```

This is the "I'm near this location, what do I need to know" quick reply.

### `0x40` — Text chunk (fallback)

For data that can't be structured (AFD narrative, custom messages). Uses dictionary compression.

```
Offset  Size  Field
0       1     0x40
1       1     chunk_seq (high nibble) | total_chunks (low nibble)
2       1     subject_type: 0=AFD, 1=custom, 2=discussion, 3=narrative
3       N     COBS(dict_compress(text))
```

Dictionary compression: A shared 128-entry `weather_dict.json` bundled with clients. During encoding, the server replaces dictionary phrases with `0xFE <code>` 2-byte escapes. On decode, clients expand them.

## Dictionary for text fields

Top-128 phrases to bundle with clients. Replaces ~20-30 bytes per occurrence with 2 bytes.

```json
{
  "version": 1,
  "phrases": [
    "SEVERE THUNDERSTORM WARNING",
    "TORNADO WARNING",
    "FLASH FLOOD WARNING",
    "FLOOD ADVISORY",
    "WIND ADVISORY",
    "IN EFFECT UNTIL",
    "REMAINS IN EFFECT",
    "til",
    "thru",
    "PM CDT",
    "AM CDT",
    "PM EDT",
    "AM EDT",
    "PM PDT",
    "AM PDT",
    "PM MDT",
    "AM MDT",
    "TAKE SHELTER NOW",
    "FOR THE FOLLOWING",
    "TAKE COVER",
    "MOVE INDOORS",
    "LOCATIONS IMPACTED",
    "DAMAGING WIND",
    "HAIL",
    "INCH",
    "INCHES",
    "QUARTER SIZE HAIL",
    "GOLF BALL SIZE HAIL",
    "TENNIS BALL SIZE HAIL",
    "BASEBALL SIZE HAIL",
    "65 MPH",
    "70 MPH",
    "60 MPH",
    "WIND GUSTS",
    "DOPPLER RADAR",
    "SEVERE THUNDERSTORM",
    "STRONG THUNDERSTORM",
    "COUNTY",
    "COUNTIES",
    "PARISH",
    "ZONE",
    "TORNADO EMERGENCY",
    "EXPIRE",
    "CANCELLED",
    "NATIONAL WEATHER SERVICE",
    "CONTINUES",
    "NORTHWEST",
    "NORTHEAST",
    "SOUTHWEST",
    "SOUTHEAST"
  ]
}
```

Expandable to 256 entries later without breaking compatibility (2-byte dict codes).

## Request/response flow

```
┌────────┐  0x02 "forecast TXZ192"  ┌────────┐
│ CLIENT │ ────────── DM ─────────▶ │  BOT   │
└────────┘                          └────┬───┘
     ▲                                   │
     │                                   │ Bot parses request
     │                                   │ Looks up ZFP for zone
     │                                   │ Builds 0x31 response
     │                                   │
     │  0x31 forecast data               │
     │  on #wx-broadcast                 │
     └───────── CHANNEL ◄────────────────┘

   All listeners on #wx-broadcast receive the 0x31 message.
   Everyone who cares caches it. Zero extra airtime for multiple interested clients.
```

Key insight: **requests are DMs (reliable), responses are broadcasts (shared)**. If three clients all want Austin's forecast within a few seconds, that's 3 DMs in + 1 broadcast out. Rate limit: one response per (data_type, location) per 5 minutes.

## Server implementation plan

1. **Add message type constants** to `protocol/meshwx.py`: `MSG_REQUEST`, `MSG_OBSERVATION`, `MSG_FORECAST`, `MSG_OUTLOOK`, `MSG_STORM_REPORTS`, `MSG_RAIN_OBS`, `MSG_METAR`, `MSG_TAF`, `MSG_WARNINGS_NEAR`, `MSG_TEXT_CHUNK`.

2. **Add pack/unpack functions** for each new type with comprehensive round-trip tests.

3. **Add location encoding module** `protocol/location_ref.py` with `pack_location(type, id)` and `unpack_location(bytes, offset)`.

4. **Build state-index table** at `geodata/state_index.json` (bundled with both client and server).

5. **Build WFO lookup file** `geodata/wfos.json` with office code → name + city.

6. **Build preload bundle generator** `scripts/build_client_data.py` that outputs a `client_data/` directory clients can copy into their app.

7. **Bot-side data extractors** that take the existing parsed text and convert to binary:
   - `protocol/encode_observation.py` — RWR/METAR → 0x30
   - `protocol/encode_forecast.py` — ZFP → 0x31
   - `protocol/encode_outlook.py` — HWO → 0x32
   - `protocol/encode_storm_reports.py` — LSR → 0x33
   - `protocol/encode_rain_obs.py` — RWR rain → 0x34

8. **Request handler** in `main.py` `_handle_dm`: detect `0x02`-prefixed DMs (hex-encoded like current `MWX` prefix), parse, dispatch to the right encoder, broadcast response.

9. **Dictionary compression** module `protocol/text_compress.py` with encode/decode + the bundled phrase list.

10. **Broadcast scheduling**: observation + forecast + outlook for bot's home location go out every hour automatically (even without requests) so listeners stay current with the bot's operator area.

## Airtime budget estimate

For a typical operator covering ~300 zones (Texas):

| Message | Count | Size | Per cycle |
|---------|-------|------|-----------|
| 0x10 Radar grids | 3 | 134 B | 402 B |
| 0x20 Warnings | 5 | ~60 B | 300 B |
| 0x30 Home obs | 1 | 16 B | 16 B |
| 0x31 Home forecast | 1 | 59 B | 59 B |
| 0x32 Home outlook | 1 | 42 B | 42 B |
| **Scheduled total/cycle** | | | **~820 B** |

At 2-second spacing between messages: ~20 seconds of airtime per cycle. One cycle per hour = 0.5% duty cycle. Very sustainable on LoRa.

Per-request responses add only a few messages each and are rate-limited.

## Versioning

Message type byte `0xF0` reserved for protocol version negotiation (future). For now, all v2 messages can coexist with v1 — clients that don't recognize a type byte ignore it harmlessly.

Dictionary version is encoded in the `weather_dict.json` file; clients refuse to decode 0x40 messages with a dictionary version newer than they have bundled.
