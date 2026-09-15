# MeshWX v5: the weather protocol for MeshCore apps

Version 5.0, 2026-09-15. This is the document an app developer builds
against. It replaces the v3/v4 protocol documents, the April 2026 iOS
brief and the v4 client guide, all of which are now superseded.

The reference encoder and decoder is `meshcore_weather/protocol/v5.py`
(pure Python, standard library only). Test vectors are in
`docs/meshwx_v5_vectors.json`; a client implementation is correct when it
decodes every vector to the JSON shown and re-encodes it to the same hex.

Design rule: the mesh carries identifiers and numbers, the phone carries
tables and words. Second rule: nothing is flooded twice unless a person
asked for it, or the mesh demonstrably did not repeat it.

---

## 1. Overview

A weather bot (a MeshCore companion radio driven by this software,
advertised as a chat node named `WX-<city>`, e.g. `WX-AUS`) receives NOAA
products from the GOES satellite and puts two things on the mesh:

1. **Binary datagrams for apps**: warnings, an active-warning digest,
   observations and forecasts, as MeshCore `GRP_DATA` packets on the
   channel `#meshwx`. Apps decode them with the tables in the preload
   bundle (section 9) and draw them.
2. **Text for people**: anyone can send commands like `wx austin tx` to the
   bot on `#meshwx` or by DM and get a plain-text reply.

An app talks to the bot by DM using the same command grammar people use,
prefixed with `>`; the bot answers on the channel as binary so every
listening app benefits from one request.

There is no discovery channel. The bot's advert (name `WX-AUS`, latitude,
longitude) is how an app finds it, and every MeshCore app already collects
adverts.

### 1.1 What the app needs

- MeshCore companion firmware **v1.15.0 or newer** on the phone's radio.
  Older firmware never delivers `GRP_DATA` to the app (it is dropped
  silently, no error).
- The channel `#meshwx` added by name (the key is derived from the name,
  as for any MeshCore hashtag channel).
- The preload bundle (`client_data/`), section 9.

### 1.2 Radio

US preset: 910.525 MHz, bandwidth 62.5 kHz, SF7, coding rate 4/5. The
Austin bot transmits at 22 dBm. A 50-byte packet costs about 260 ms of
airtime, a 100-byte packet about 400 ms; a flood is repeated once by every
repeater in range. Keep requests to user actions; never poll.

---

## 2. Transport

### 2.1 The packet

Every v5 message is the `data` of one MeshCore `GRP_DATA` packet (payload
type 6) on `#meshwx`, with `data_type = 0xFF10` (MeshCore development
range; a permanent number will be requested upstream and announced in
`protocol.json`).

What the firmware puts on air (for reference, the app never sees this
layer): `[channel hash 1][MAC 2][AES-128-ECB(channel key) of
[data_type u16 LE][data_len u8][data]]`. What the companion delivers to
the app (`RESP_CODE_CHANNEL_DATA_RECV`, 0x1B): SNR, channel index, path
length, `data_type`, `data_len`, `data`. Ignore any `data_type` other
than `0xFF10`.

`data` is at most **165 bytes**. Every message in this spec fits in one
packet; there is no fragmentation except the text message, which carries
its own chunk numbers.

### 2.2 Common header (4 bytes)

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 1 | `seq` | Per-bot sequence number, increments by 1 for every new message, wraps 255 to 0 |
| 1 | 2 | `bot` | First two bytes of the bot's public key, little-endian u16 (from its advert) |
| 3 | 1 | `type` | High nibble: message type. Low nibble: type-specific flags |

Message types:

| Type | Name | Section |
|---|---|---|
| 1 | Warning | 3 |
| 2 | Cancel | 4 |
| 3 | Digest | 5 |
| 4 | Observations | 6 |
| 5 | Forecast | 7 |
| 6 | Text | 8 |
| 7 | Not available | 8.3 |
| 8 to 11 | Reserved for future structured products | |
| 12 to 15 | Free for third-party experiments; the bot never sends them | |

Receivers ignore unknown types.

### 2.3 Duplicates and ordering

- The bot may transmit the **same bytes twice**: it listens for a
  repeater's echo of every packet and sends it once more, byte for byte
  identical, when it hears none. MeshCore nodes dedupe by packet hash, so
  the phone's radio normally never delivers the copy; if it does, `(bot,
  seq)` is the same and the app drops it.
- A **new** message always has a new `seq`. Track the last `seq` per
  `bot`; a gap means a missed message and is the cue to ask for the
  digest (`>d`).
- Warnings are keyed by identity `(event, office, etn)`, not by `seq`:
  a warning message with a known identity replaces the stored one.

### 2.4 Integers

All multi-byte integers are **little-endian** (MeshCore's own convention).
Signed bytes are two's complement. Times are Unix minutes (seconds / 60)
as u32 unless stated.

---

## 3. Warning (type 1)

Sent when a warning, watch or advisory becomes active in the bot's
coverage, and again when something material changes (expiry moved by 30
minutes or more, tags changed, area changed). Not sent for wording-only
updates.

Flags nibble: bit 0 = update of an identity already sent (informational;
the app replaces by identity either way).

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 1 | `event` | VTEC event code, `protocol.json` `events` (e.g. `SV.W` = 3). Name and long name from `event_names`. Severity from the significance letter: W warning, A watch, Y advisory, S statement |
| 5 | 1 | `office` | Index into `index.json` `offices` (issuing NWS office, e.g. `EWX`) |
| 6 | 2 | `etn` | u16, event tracking number. `(event, office, etn)` is the identity |
| 8 | 4 | `expires` | u32 Unix minutes, absolute. Show a countdown from the phone's clock; treat as expired when passed |
| 12 | 1 | `tags` | bits 7-6 tornado: 0 none, 1 possible, 2 radar indicated, 3 observed. bits 5-4 flood source: 0 none, 1 radar, 2 radar and gauge, 3 observed. bits 3-2 flood damage: 0 none, 1 considerable, 2 catastrophic. bit 1: polygon follows. bit 0: area list follows |
| 13 | 1 | `hail` | Hail tag in quarter inches (4 = 1.00 in). 0 = none |
| 14 | 1 | `wind` | Wind tag in mph. 0 = none |

Then, if `tags` bit 1 is set, a polygon:

| Size | Field | Meaning |
|---|---|---|
| 1 | `n` | Vertex count, 3 to 30 |
| 3 | `lat0` | i24 LE, degrees × 10000 |
| 3 | `lon0` | i24 LE, degrees × 10000 |
| 4 × (n−1) | deltas | For each further vertex: `dlat` i16 LE, `dlon` i16 LE, in 0.001° relative to the previous vertex |

Then, if `tags` bit 0 is set, an area list:

| Size | Field | Meaning |
|---|---|---|
| 1 | `k` | Number of runs, 1 to 30 |
| 4 × k | runs | Each: `state` u8 (bit 7 = 1 for a county, 0 for a forecast zone; bits 6-0 = index into `index.json` `states`), `start` u16 LE, `run` u8. The run covers UGC numbers `start` … `start + run − 1`. Example: `TXZ191` to `TXZ194` = state TX, zone, start 191, run 4 |

Storm-based warnings (tornado, severe thunderstorm, flash flood) carry the
polygon and usually a county list. Zone-based products (winter, heat,
wind, fire) carry the zone list only. Draw the polygon when present and
name the counties under it; otherwise fill the listed zones or counties
from `zones.geojson` / `counties.geojson`.

Typical size: a severe thunderstorm warning with 6 vertices and 2
counties is 15 + 27 + 9 = 51 bytes.

## 4. Cancel (type 2)

Sent once when a warning ends before its stored expiry (VTEC action CAN,
EXP or UPG). Flags nibble: 0 cancelled, 1 expired early, 2 upgraded (a
new warning with the replacement follows).

| Offset | Size | Field |
|---|---|---|
| 4 | 1 | `event` |
| 5 | 1 | `office` |
| 6 | 2 | `etn` u16 |

8 bytes total. Remove the identity.

## 5. Digest (type 3)

The list of everything active in the bot's coverage, sent every 3 hours,
one minute after any cancel, and on request (`>d`). This is how an app
recovers from missed packets: any identity in the digest the app does
not hold is one `>w <identity>` request away.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 4 | `now` | u32 Unix minutes when the digest was built. Entries are relative to it, so a message drained from an offline queue hours later still decodes correctly |
| 8 | 1 | `feed_health` | Minutes since the bot last received any product from its home office, in units of 4 minutes, capped at 255 (17 hours or more, or unknown). Show "feed stale" above about 60 (4 hours): silence from the bot then does not mean calm weather |
| 9 | 1 | `count` | 0 to 25 |
| 10 | 6 × count | entries | Each: `event` u8, `office` u8, `etn` u16 LE, `expires_rel` u16 LE minutes after `now` |

An identity the app holds that is absent from the digest has ended:
remove it.

## 6. Observations (type 4)

Current conditions for the METAR stations in the bot's coverage, batched
in one packet, every hour (every 30 minutes while a tornado or severe
thunderstorm warning is active in coverage), and on request.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 4 | `ts` | u32 Unix minutes of the newest observation in the batch |
| 8 | 1 | `n` | Station count, 1 to 14 |
| 9 | 11 × n | stations | Below |

Per station (11 bytes):

| Size | Field | Meaning |
|---|---|---|
| 2 | `station` | u16 LE index into `index.json` `stations` (ICAO list) |
| 1 | `temp` | i8 °F; −128 = unknown |
| 1 | `dewpoint` | i8 °F; −128 = unknown |
| 1 | `dir_sky` | High nibble: wind direction as a 16-point compass (0 N, 4 E, 8 S, 12 W; 0 with speed 0 = calm). Low nibble: sky code (`protocol.json` `sky_codes`) |
| 1 | `wind` | u8 mph; 255 = unknown |
| 1 | `gust` | u8 mph; 0 = none |
| 1 | `visibility` | u8 statute miles; 255 = unknown |
| 1 | `pressure` | u8, (inHg − 29.00) × 100; 255 = unknown. 92 = 29.92 |
| 1 | `humidity` | u8 percent; 255 = unknown |
| 1 | `feels` | i8, feels-like minus temperature in °F (heat index or wind chill); 0 = same |

A single-station request (`>o KAUS`) is the same message with `n = 1`.

## 7. Forecast (type 5)

A point forecast (NWS PFM), seven periods, every 6 hours for the bot's
home point and on request for any point.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 2 | `point` | u16 LE index into `pfm_points.json` `points`; 0xFFFF = a place with no bundled point (the bot resolved it to the nearest point; use the request you sent to label it) |
| 6 | 4 | `issued` | u32 Unix minutes the forecast was issued |
| 10 | 1 | `first` | Period id of the first entry: 0 today, 1 tonight, 2 tomorrow, 3 tomorrow night, and so on (even = day, odd = night, day offset = id ÷ 2 from the issue date) |
| 11 | 1 | `n` | Period count, 1 to 14 |
| 12 | 5 × n | periods | Below |

Per period (5 bytes):

| Size | Field | Meaning |
|---|---|---|
| 1 | `high` | i8 °F; 127 = not given (night periods) |
| 1 | `low` | i8 °F; 127 = not given (day periods) |
| 1 | `pop` | u8 probability of precipitation, percent; 255 = not given |
| 1 | `cond` | Low nibble: sky code. High nibble flags: bit 4 thunder, bit 5 wintry (snow, sleet, freezing rain), bit 6 windy (sustained 20 mph or more), bit 7 fog or haze |
| 1 | `wind` | High nibble: direction (16-point compass). Low nibble: speed ÷ 5 mph (15 = 75 or more) |

## 8. Text (type 6) and Not available (type 7)

### 8.1 Text

Anything narrative: a warning's full text, the forecast discussion,
storm reports, rainfall, raw METAR/TAF, the hazardous weather outlook,
space weather. Request only; never broadcast on a schedule.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 1 | `subject` | 0 warning narrative, 1 forecast discussion (AFD), 2 space weather, 3 storm reports, 4 rainfall, 5 METAR/TAF raw, 6 hazardous outlook, 7 nowcast, 8 general |
| 5 | 1 | `group` | Same value for every chunk of one reply (the `seq` of its first chunk) |
| 6 | 1 | `idx` | Chunk number, from 0 |
| 7 | 1 | `total` | Chunks in this reply, 1 to 8 |
| 8 | ≤157 | `text` | UTF-8, never split inside a code point |

Reassemble by `(bot, group)` in `idx` order; show partial text with a
"missing part" marker if a chunk never arrives (ask again after 20 s,
at most once).

### 8.2 Request grammar (app side)

Requests are DMs to the bot. An app request starts with `>`; the answer
comes back on `#meshwx` as v5 messages, never as a DM. The same commands
without `>` are what people type and get a text DM back.

| Request | Answer |
|---|---|
| `>d` | Digest |
| `>w` | One Warning message per active warning in coverage (at most 6, newest first), then a Digest |
| `>w SV.W.EWX.42` | That one warning (identity as `event.office.etn` with the office's 3-letter code) |
| `>w TXC453` or `>w TXZ192` | Every active warning touching that county or zone (at most 6) |
| `>wt SV.W.EWX.42` | The warning's narrative as Text, subject 0 |
| `>o` | Observations for the coverage stations |
| `>o KAUS` | Observations, one station |
| `>f` | Forecast for the bot's home point |
| `>f 102` | Forecast for point index 102 |
| `>f round rock tx` | Forecast for a place the bot resolves (nearest point; `point` may be 0xFFFF) |
| `>afd EWX` | Forecast discussion, Text subject 1 |
| `>space` | Space weather summary, Text subject 2 |
| `>storm TX` `>rain TX` `>metar KAUS` `>taf KAUS` `>hwo` | Text, subjects 3, 4, 5, 5, 6 |

Rules the bot applies: one request per sender every 5 seconds; an
identical request within 5 minutes gets the cached answer re-sent with a
new `seq`; a request the bot cannot serve gets a Not available message.
Wait up to 15 seconds for an answer before showing a failure; retry once,
then tell the user the bot may be out of range.

### 8.3 Not available (type 7)

| Offset | Size | Field |
|---|---|---|
| 4 | 1 | `request`: ASCII code of the request's first letter (`w`, `o`, `f`, `a`, `s`, `r`, `m`, `t`, `h`, `d`) |
| 5 | 1 | `reason`: 0 no data yet, 1 unknown location, 2 unsupported, 3 bot error, 4 rate limited (try later) |

---

## 9. The preload bundle (`client_data/`)

Ship these files in the app. Everything the wire refers to by index lives
here; the bot never sends names.

| File | Size | Contents | Used for |
|---|---|---|---|
| `protocol.json` | 11 KB | Version, message types, enums: `events` (code → `TO.W`), `event_names` (`short`, `long`), `sky_codes`, `text_subjects`, `not_available_reasons`, and the v5 tables | Every decode |
| `index.json` | ~20 KB | `offices`: ordered list of NWS office codes (the `office` byte). `stations`: ordered ICAO list (the `station` u16). `states`: ordered state/territory codes (the `state` byte, bits 6-0). Append-only: an index never changes meaning | Warning, digest, observations |
| `stations.json` | 185 KB | ICAO → name, state, lat, lon | Station search, labels, map pins |
| `pfm_points.json` | 104 KB | `points`: ordered list `[name, office, lat, lon, zone]`; the list position is the `point` u16 | Forecast labels, "forecast for my location" (nearest point by distance) |
| `places.json` | 1.4 MB | `places`: list `[NAME, ST, lat, lon, population]` | Place search and autocomplete |
| `zones.json` | 355 KB | Zone id (`TXZ192`) → name, office, state, lat, lon | Naming the areas of a warning; zone lookup for a place |
| `zones.geojson` | 10 MB | Zone polygons (`code` property, e.g. `TXZ192`) | Filling a zone-based warning on the map. Optional download; the app can fall back to the zone centroid pin |
| `counties.json` | 227 KB | County UGC (`TXC453`) → name, state, representative lat/lon | Naming the counties of a storm-based warning; centroid pin |
| `counties.geojson` | 4.8 MB | County polygons (Census cartographic boundaries, 1:5M, `code`/`name`/`state` properties), Polygon or MultiPolygon | Filling counties on the map when a warning has no polygon, and as the outline under one. Optional download like `zones.geojson` |
| `wfos.json` | 9 KB | Office code → states, lat, lon | Office names, `>afd` picker |
| `weather_dict.json`, `regions.json`, `state_index.json` | | Legacy (v3/v4). Not used by v5; `state_index.json` is the same list as `index.json` `states` | |

Area runs decode to UGC codes: state code from `index.json` `states`,
then `C` or `Z`, then the 3-digit number. `TXC453` is in `counties.json`,
`TXZ192` in `zones.json`; both have polygons in the matching GeoJSON.
Louisiana parishes, Alaska boroughs and Virginia's independent cities are
all "counties" here, as in the NWS products.

Bundle versioning: `protocol.json` `version` (8 for v5.0). The bot's
advert does not carry a version; a bump is announced in the repository.

## 10. Rendering

### 10.1 Sky codes and icons

| Code | Meaning | SF Symbol suggestion |
|---|---|---|
| 0 | clear | `sun.max` (day), `moon.stars` (night) |
| 1 | few clouds | `cloud.sun` / `cloud.moon` |
| 2 | scattered | `cloud.sun` / `cloud.moon` |
| 3 | broken | `cloud` |
| 4 | overcast | `cloud.fill` |
| 5 | fog | `cloud.fog` |
| 6 | smoke | `smoke` |
| 7 | haze | `sun.haze` |
| 8 | rain | `cloud.rain` |
| 9 | snow | `cloud.snow` |
| 10 | thunderstorm | `cloud.bolt.rain` |
| 11 | drizzle | `cloud.drizzle` |
| 12 | mist | `cloud.fog` |
| 13 | squall | `wind` |
| 14 | sand or dust | `sun.dust` |
| 15 | other | `cloud` |

Forecast `cond` flags override the base icon: thunder → `cloud.bolt.rain`,
wintry → `cloud.sleet` or `cloud.snow`, windy → add `wind`, fog → `cloud.fog`.

### 10.2 Warnings

Colour by event, falling back to significance: warnings (W) red family,
watches (A) yellow, advisories (Y) orange or blue, statements (S) grey.
NWS conventions for the common ones:

| Event | Colour | Icon |
|---|---|---|
| TO.W tornado warning | red | `tornado` |
| TO.A tornado watch | yellow | `tornado` |
| SV.W severe thunderstorm warning | orange | `cloud.bolt` |
| SV.A severe thunderstorm watch | light orange | `cloud.bolt` |
| FF.W flash flood warning | dark green | `water.waves` |
| FA.W / FL.W flood warning | green | `water.waves` |
| FA.Y / FL.Y flood advisory | light green | `drop` |
| HT.Y heat advisory, EH.W extreme heat | orange-red | `thermometer.sun` |
| WS.W winter storm, BZ.W blizzard | pink / purple | `snowflake` |
| WW.Y winter weather advisory | lavender | `snowflake` |
| HW.W high wind, WI.Y wind advisory | tan | `wind` |
| FW.W red flag | magenta | `flame` |
| Anything else | by significance | `exclamationmark.triangle` |

Show the tags when non-zero: "Hail 1.00 in", "Wind 60 mph", "Tornado:
radar indicated", "Flash flood damage: considerable". Show "expires in
42 min" from `expires` and the phone's clock. Sort by severity then
expiry.

### 10.3 Observations and forecasts

Temperatures are whole °F; show feels-like when `feels` is non-zero.
Wind: "WNW 15 gusting 26". Pressure: `29.00 + pressure/100` inHg. A
station is "stale" when `ts` is older than 2 hours. A forecast is stale
after 12 hours from `issued`.

### 10.4 Text fallback for people

The bot's text commands (send on `#meshwx` or by DM; replies come by DM,
long ones paged with "(1/3) more", send `more` for the next page):

```
wx <city ST>        current conditions, today's high/low, active warnings
forecast <city ST>  next days, one line per day
warn <city ST|ST>   active watches, warnings, advisories
storm <ST>          storm reports, last 6 hours
rain <ST>           rainfall totals
metar <ICAO>        latest airport observation
space               space weather
more                next page of the last long reply
help                the command list
```

An app can expose this as a "message the bot" screen for anything the
binary path does not cover.

## 11. Search and place resolution

- **Places**: `places.json` entries are `[NAME, ST, lat, lon, population]`.
  Match by prefix on the name, then rank by distance to the user (or the
  bot), then by population. Always show the state; 207 names appear in
  more than one state. Round Rock exists in TX and AZ.
- **Stations**: `stations.json` by ICAO prefix or by name substring; show
  distance.
- **Points**: for "forecast here", take the nearest `pfm_points` entry by
  haversine distance and send `>f <index>`; label the result with the
  point name and the distance.
- **Zones**: `zones.json` by id or name. Zone from a coordinate needs the
  polygons (`zones.geojson`); without them use the nearest zone centroid
  and label it as approximate.
- When the app sends a place string (`>f round rock tx`), the bot
  resolves it the same way (prefix match, nearest to the bot, then
  population) and answers with the nearest point; the `point` field tells
  the app which one.

## 12. Discovery and several bots

- A bot is any advert whose name starts with `WX-`. Its position is in
  the advert. Show the nearest; let the user pick.
- Every message carries `bot` (two bytes of the public key). Keep
  separate state per bot; when two bots cover the same place you may
  hear the same warning identity from both, and it is the same warning.
- Send requests to the bot you selected. A request naming a place is
  answered only by the nearest bot to that place if bots can hear each
  other; the bot you asked always answers requests that do not name a
  place.

## 13. Airtime etiquette

- Never poll. Request on user action, and at most once per 5 seconds.
- Prefer the digest over `>w` when you only need to know what is active.
- Do not re-request something you received in the last 5 minutes; the
  bot would only re-send the cached bytes.
- Listen passively: the scheduled broadcasts (warnings on change, digest
  every 3 h, observations hourly, home forecast every 6 h) cover the
  common case without any request.

## 14. Build checklist

1. Add `#meshwx`; confirm firmware ≥ 1.15 on the radio.
2. Decode `GRP_DATA` with `data_type 0xFF10`; run the test vectors.
3. Track `(bot, seq)`; dedupe; detect gaps → `>d`.
4. Warnings keyed by `(event, office, etn)`; apply Cancel and Digest.
5. Render from the bundle tables; never from strings on the wire.
6. Requests by DM with `>`; 15 s timeout, one retry.
7. Stale badges from `ts`, `issued`, `expires`, `feed_health`.
8. Text fallback screen with the human commands and `more`.

## 15. What changed from v4

For anyone who read the April 2026 brief: the data channel is `#meshwx`
(same channel as the text), datagrams are `GRP_DATA` (no name prefix, no
COBS, no v4 frame header), requests are text with a `>` prefix instead of
`WXQ` + hex, the discovery channel and beacon are gone, the headline is no
longer on the wire (names come from the bundle), warnings carry the VTEC
event byte and storm tags, areas are runs of zone or county numbers, and
a cancel and a digest exist. Observations are batched. Message types are
renumbered; nothing from v3/v4 decodes as v5.
