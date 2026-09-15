# MeshWX v5: the weather protocol for MeshCore apps

Version 5.0, revision 3, 2026-09-15. This is the document an app developer
builds against. It replaces the v3/v4 protocol documents, the April 2026
iOS brief and the v4 client guide, all of which are now superseded.

Revision 3 fixes bot behaviour that disagreed with revision 2 and states
rules revision 2 left out: when `seq` is assigned, how a full digest may be
cut, unknown observation fields, what `first` counts from, the request
limits, named-station METAR and TAF replies, `>f <index>`, and two offices
appended to the bundle. It also adds `>sat`, and US ZIP codes as places
with the bundle file `zips.json`. The wire layout did not change. If you
hold revision 2, read section 16 first.

Revision 2 corrected statements in sections 1, 6, 7, 8.2, 8.3, 12 and 13
that described behaviour this bot does not have. If you hold revision 1,
read section 17 as well.

The reference encoder and decoder is `meshcore_weather/protocol/v5.py`
(pure Python, standard library only). Test vectors are in
`docs/meshwx_v5_vectors.json`; a client implementation is correct when it
decodes every vector to the JSON shown and re-encodes it to the same hex.

The JSON keys in the vectors are the reference decoder's, and they are
friendlier than the wire field names used in the tables below. The wire's
`issued` decodes as `issued_min`, `first` as `first_period`, `pop` as
`pop_pct`, and `cond` and `wind` expand into `sky`, `thunder`, `wintry`,
`windy`, `fog`, `wind_dir_deg`, `wind_dir` and `wind_mph`.

Design rule: the mesh carries identifiers and numbers, the phone carries
tables and words. Second rule: nothing is flooded twice unless a person
asked for it, the mesh demonstrably did not repeat it, or it is a new
life-safety warning (section 3).

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

An app talks to the bot using the same command grammar people use,
prefixed with `>`, sent either as a DM or on `#meshwx`; the bot answers on
the channel as binary so every listening app benefits from one request.

There is no discovery channel. The bot's advert (name `WX-AUS`) is how an
app finds it, and every MeshCore app already collects adverts. Do not rely
on the advert's position: this bot never sets the firmware's advert
location policy, so its adverts carry latitude and longitude 0,0 even
though the node knows where it is.

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
than `0xFF10` (the bot's operator link test uses `0xFF1E`, a 6-byte
datagram; it carries nothing for the app).

`data` is at most **165 bytes**. Every message in this spec fits in one
packet; there is no fragmentation except the text message, which carries
its own chunk numbers.

### 2.2 Common header (4 bytes)

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 1 | `seq` | Per-bot sequence number, one more for every packet the bot transmits, wraps 255 to 0 (section 2.3) |
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

### 2.3 Sequence numbers, duplicates and ordering

- `seq` is assigned when a packet is transmitted, not when it is built.
  Scheduled broadcasts and request answers share one counter, and a
  number is used only by a packet the bot's radio accepted, so the bot
  itself never leaves a gap.
- The bot transmits one batch at a time: a request answer never
  interleaves with a scheduled broadcast, and packets leave the bot in
  `seq` order, except a resend (below), which can follow later packets.
  The mesh can also deliver packets in a different order, since two floods
  may take different paths.
- The counter is saved to disk and continues after a restart. It is saved
  past each batch before the batch starts, so a bot killed part-way comes
  back ahead of every number it may have used: the app sees a gap, never
  a repeated `seq`.
- The bot may transmit the **same bytes twice**: it listens for a
  repeater's echo of every packet and, when it hears none within about
  8 seconds, sends it once more, byte for byte identical and with the same
  `seq`. MeshCore nodes
  dedupe by packet hash, so the phone's radio normally never delivers the
  copy; if it does, `(bot, seq)` is the same and the app drops it.
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
coverage, and again when something material changes: the expiry changed
(to the minute), tags changed, area changed. Not sent for wording-only
updates. The one exception to the minute rule is an expiry the bot had to
invent (a product in force until further notice is given one 12 hours
ahead): that counts only when it moves by 30 minutes.

Flags nibble: bit 0 = update of an identity already sent (informational;
the app replaces by identity either way).

A new tornado (`TO.W`), severe thunderstorm (`SV.W`), flash flood (`FF.W`)
or extreme wind (`EW.W`) warning is sent once more about two minutes after
the first: the same warning under a new `seq`, flag bit 0 clear.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 1 | `event` | VTEC event code, `protocol.json` `events` (e.g. `SV.W` = 3). Name and long name from `event_names`. Severity from the significance letter: W warning, A watch, Y advisory, S statement |
| 5 | 1 | `office` | Index into `index.json` `offices`: the issuing office, a WFO (e.g. `EWX`) or a national centre (`NHC`, or `WNS` for the Storm Prediction Center). A product from an office the bundle does not list is not sent at all |
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

Sent once when a warning the bot has sent stops being active (VTEC action
CAN, EXP or UPG) more than 5 minutes before its stored expiry. Flags
nibble: 0 cancelled, 1 expired early, 2 upgraded (a new warning with the
replacement follows). This bot always sends 0, whatever the reason, so do
not wait for a replacement.

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
| 8 | 1 | `feed_health` | Minutes since the bot last received any product from its home office, in units of 4 minutes, capped at 255. 255 means nothing has ever been received |
| 9 | 1 | `count` | 0 to 25 |
| 10 | 6 × count | entries | Each: `event` u8, `office` u8, `etn` u16 LE, `expires_rel` u16 LE minutes after `now`. Sorted by expiry, soonest first |

An identity the app holds that is absent from the digest has ended:
remove it, with one exception. The digest lists at most 25 identities, the
25 that expire soonest, so when `count` is 25 the list may have been cut.
In that case keep a held identity whose expiry is at or after the last
listed entry's; it may simply not have fitted.

`feed_health` measures one office's quietness, not the satellite link. The
home office is the one resolved from the bot's home coordinate unless the
operator listed offices explicitly; for WX-AUS it is EWX. A calm night at
a single office pushes this past 60 (4 hours) while the feed is perfectly
healthy, so word it as "the bot has not heard from EWX for 5 h" rather
than as a broken feed. Only 255 justifies telling the user that alerts may
not be reaching them at all.

Known limitation: a quiet home office and a dead satellite feed are
indistinguishable in this byte. The bot does not currently publish a
whole-feed liveness figure.

## 6. Observations (type 4)

Current conditions for the METAR stations in the bot's coverage, batched
in one packet, every hour, and on request. The interval is fixed: this bot
does not speed observations up during severe weather.

The station list is not fixed either. It is recomputed for every batch:
stations inside the coverage radius that have filed a METAR in the last
120 minutes, nearest first, at most 14. Stations drop out when they stop
reporting, so do not treat the batch as a stable description of what the
bot covers.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 4 | `ts` | u32 Unix minutes of the newest report in the batch (when the bot's feed received it). Each station's report may be up to 120 minutes older than `ts` |
| 8 | 1 | `n` | Station count, 1 to 14 |
| 9 | 11 × n | stations | Below |

Per station (11 bytes):

| Size | Field | Meaning |
|---|---|---|
| 2 | `station` | u16 LE index into `index.json` `stations` (ICAO list) |
| 1 | `temp` | i8 °F; −128 = unknown (in practice never: a report without a temperature group is not sent) |
| 1 | `dewpoint` | i8 °F; −128 = unknown |
| 1 | `dir_sky` | High nibble: wind direction as a 16-point compass (0 N, 4 E, 8 S, 12 W; 0 with speed 0 = calm). The nibble has no unknown value: a variable wind (VRB) or a report without a wind group also sends 0, so 0 with a speed above 0 means north or variable. Low nibble: sky code (`protocol.json` `sky_codes`); 15 when the report has no cloud or weather group |
| 1 | `wind` | u8 mph; 255 = unknown |
| 1 | `gust` | u8 mph; 0 = none |
| 1 | `visibility` | u8 whole statute miles, rounded down (1/2SM and M1/4SM are 0, 1 1/2SM is 1); 255 = unknown |
| 1 | `pressure` | u8, (inHg − 29.00) × 100; 255 = unknown, also sent for a reading outside 29.00 to 31.54 inHg. 92 = 29.92 |
| 1 | `humidity` | u8 percent; 255 = unknown (temperature or dewpoint unknown) |
| 1 | `feels` | i8, feels-like minus temperature in °F (heat index or wind chill); 0 = same |

A group the METAR did not carry is sent as that field's unknown value; the
bot never fills in a default. A station whose report still cannot be
encoded is left out, and the rest of the batch is sent.

A single-station request (`>o KAUS`) is the same message with `n = 1`.

## 7. Forecast (type 5)

A point forecast (NWS PFM) as **whole days**, up to seven of them, for the
bot's home point every 6 hours and on request for any point.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 2 | `point` | u16 LE index into `pfm_points.json` `points`. For `>f <index>` it is the index you asked for whenever the forecast is at that point's coordinates (some points share coordinates, e.g. 1617 and 1840); a forecast from another point carries that point's own index. 0xFFFF = a place with no bundled point (the bot resolved it to the nearest point; use the request you sent to label it) |
| 6 | 4 | `issued` | u32 Unix minutes the forecast was issued |
| 10 | 1 | `first` | Period id of the first entry. The id space numbers half-days from the issue date, which is the local date of `issued` in the point's own time zone: even = day, odd = night, day offset = id / 2. This bot emits whole days only, so `first` is always even and the first entry covers day `first / 2`. An evening issuance usually has no usable rest of today, so its first entry is tomorrow and `first` is 2 |
| 11 | 1 | `n` | Entry count, 1 to 14. In practice 1 to 7, one per day |
| 12 | 5 x n | periods | Below, consecutive days |

Per period (5 bytes):

| Size | Field | Meaning |
|---|---|---|
| 1 | `high` | i8 degrees F, the day's high; 127 = not available |
| 1 | `low` | i8 degrees F, the night's low; 127 = not available |
| 1 | `pop` | u8 probability of precipitation, percent; 255 = not given |
| 1 | `cond` | Low nibble: sky code. High nibble flags: bit 4 thunder, bit 5 wintry, bit 6 windy, bit 7 fog (when this bot sets each: below) |
| 1 | `wind` | High nibble: direction (16-point compass). Low nibble: speed / 5 mph (15 = 75 or more) |

Label the entries from the issue date: entry `i` is the local date of
`issued` plus `first / 2 + i` days.

This bot sets a `cond` flag when any 3- or 6-hourly slot of that local day
qualifies: thunder for a thunderstorm chance, likely or definite; wintry
for sleet, freezing rain, freezing drizzle or blowing snow (a cold, dry
night does not set it); windy for sustained wind of 30 mph or more or gusts
of 40 mph or more; fog for fog or patchy fog, never haze.

Every entry carries both a high and a low, so render one row per day. A
127 in either field means that half of the day is missing from the source
product, which happens at the edges of the PFM window. It does not mark a
night period.

The half-day id space is kept so a later bot can send day and night
entries without a format change. A client that sees an odd `first`, or
entries where one temperature is always 127, is talking to such a bot and
should fall back to alternating rows.

Two forecast vectors ship in `meshwx_v5_vectors.json`, and a client should
decode both. `forecast_seven_days` is what this bot sends: seven whole
days, even `first`, every entry carrying a high and a low.
`forecast_seven_periods` is the reserved half-day form: odd `first`, with
the temperatures alternating between 127 and a value.

## 8. Text (type 6) and Not available (type 7)

### 8.1 Text

Anything narrative: a warning's full text, the forecast discussion,
storm reports, rainfall, raw METAR/TAF, the hazardous weather outlook,
space weather, the bot's receiver status. Request only; never broadcast
on a schedule.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 1 | `subject` | 0 warning narrative, 1 forecast discussion (AFD), 2 space weather, 3 storm reports, 4 rainfall, 5 METAR/TAF raw, 6 hazardous outlook, 7 nowcast (defined, not sent by this bot), 8 general |
| 5 | 1 | `group` | Same value for every chunk of one reply: the `seq` its first chunk was transmitted with |
| 6 | 1 | `idx` | Chunk number, from 0 |
| 7 | 1 | `total` | Chunks in this reply, 1 to 8 |
| 8 | ≤157 | `text` | UTF-8, never split inside a code point |

Reassemble by `(bot, group)` in `idx` order; show partial text with a
"missing part" marker if a chunk never arrives (ask again after 20 s,
at most once).

### 8.2 Request grammar (app side)

An app request starts with `>` and may be sent either as a DM to the bot
or as text on `#meshwx`. Either way the answer comes back on `#meshwx` as
v5 messages, never as a DM. The same commands without `>` are what people
type and get a text DM back.

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
| `>f 102` | Forecast for point index 102; `point` is 102 whenever the forecast is at that point's coordinates (section 7) |
| `>f round rock tx` | Forecast for a place the bot resolves (nearest point; `point` may be 0xFFFF) |
| `>f 78701`, `>f 78701-1234` | Forecast for a US ZIP, looked up in `zips.json` (section 9) and answered like a place. A ZIP not in the table gets Not available reason 1 |
| `>afd EWX` | Forecast discussion, Text subject 1 |
| `>space` | Space weather summary, Text subject 2 |
| `>metar KAUS` | That station's own METAR from the last 120 minutes, Text subject 5, starting `METAR KAUS`. Never another station's: without one, Not available reason 0, request `m` |
| `>taf KAUS` | That station's own current TAF, Text subject 5, starting `TAF KAUS` (an amendment reads `TAF KAUS AMD ...`). Never another station's: without one, Not available reason 0, request `t` |
| `>metar round rock tx`, `>taf round rock tx`, bare `>metar` / `>taf` | The nearest station with a report (the bot's home without an argument), Text subject 5, labelled with that station and its distance, e.g. `METAR (KGTU 15km) KGTU 151155Z ...` |
| `>storm TX` `>rain TX` `>hwo` | Text, subjects 3, 4, 6 |
| `>sat` | The bot's GOES receiver now, one line, Text subject 8: lock, signal good/fair/poor, packets dropped in the last minute, age of the newest EMWIN file. A receiver that is not reporting is answered as Text saying so. A Not available for it would carry `s`, the letter `>space` and `>storm` use |

A request "names a station" when its argument is a 4-character ICAO code
the bot knows; anything else is resolved as a place. For `>f`, 5 digits (or
ZIP+4, `78701-1234`) is a ZIP and 1-4 digits is a point index. A ZIP also
works wherever a place does (`>metar 78701`, `>taf 78701`, `>hwo 78701`);
`>w` and `>o` take no ZIP (the app resolves one to UGCs and stations itself).

Rules the bot applies:

- One request per sender every 5 seconds.
- A budget of 60 answer **packets** per hour across all senders (packets,
  not requests: a bare `>w` can take 7). It is checked before an answer is
  built. Once 60 packets went out in the last hour, requests get no reply;
  the answer that crosses 60 still goes out whole.
- A `>` request sent **as a DM** first passes the limiter for people's
  text commands: one reply per sender every 5 seconds, at most 40 per
  sender per hour and 400 per hour in total, counted together with text
  commands. A `>` line **on `#meshwx`** skips that limiter and meets only
  the two limits above.
- A request the bot cannot serve gets a Not available message.

Every limit is enforced silently: a throttled request produces no reply at
all (see 8.3).

There is no cache. Every answer is built from the bot's state at the
moment it replies, so a Digest you receive is always current and can be
trusted immediately.

Wait up to 15 seconds for an answer before showing a failure; retry once,
then tell the user the bot may be out of range.

**Coverage and place arguments.** A request that names a place is served
nationwide. Coverage filters only the scheduled broadcasts and the three
argument-free requests `>d`, `>w` and `>o`. What limits a distant answer is what
the EMWIN satellite feed carries, not policy.

**A Digest follows the bare `>w` only.** `>w TXC453` and
`>w SV.W.EWX.42` answer with warnings alone, so do not wait for a Digest
to mark those replies complete.

**County codes versus zone codes.** Storm-based warnings (tornado, severe
thunderstorm, flash flood) carry county codes such as `TXC453`.
Advisories, watches and most other products carry public zone codes such
as `TXZ192`. A county query matches nothing while only zone-coded products
are active, and the reverse. When you do not know which is in force, query
the zone.

### 8.3 Not available (type 7)

| Offset | Size | Field |
|---|---|---|
| 4 | 1 | `request`: ASCII code of the request's first letter (`w`, `o`, `f`, `a`, `s`, `r`, `m`, `t`, `h`, `d`) |
| 5 | 1 | `reason`: 0 no data yet, 1 unknown location, 2 unsupported, 3 bot error, 4 rate limited (try later) |

`reason` 0 is ambiguous in this bot. It is sent when nothing is active for
a place, when the bot holds no data for it, and when a named station has
no current METAR or TAF. Treat it as "no answer available right now", not
as "try again shortly".

`reason` 4 is defined but never sent. None of the bot's limits replies, so
silence means either out of range or throttled, and the app cannot tell
which.

---

## 9. The preload bundle (`client_data/`)

Ship these files in the app. Everything the wire refers to by index lives
here; the bot never sends names.

| File | Size | Contents | Used for |
|---|---|---|---|
| `protocol.json` | 14 KB | `version`, `events` (code → `TO.W`), `event_names` (`short`, `long`), `sky_codes`, and under `v5` the message types, flags, `text_subjects`, `not_available_reasons`, tags, limits and sentinels. The top-level `messages`, `data_types`, `text_subjects` and `not_available_reasons` are v4 tables with other numbers: do not use them for v5 | Every decode |
| `index.json` | 17 KB | `offices`: ordered list of office codes (the `office` byte): the 125 WFOs in alphabetical order, then the national centres `NHC` (125, National Hurricane Center) and `WNS` (126, Storm Prediction Center). `stations`: ordered ICAO list (the `station` u16). `states`: ordered state/territory codes (the `state` byte, bits 6-0). Append-only: new entries go at the end, so an index never changes meaning | Warning, digest, observations |
| `stations.json` | 185 KB | ICAO → name, state, lat, lon | Station search, labels, map pins |
| `pfm_points.json` | 104 KB | `points`: ordered list `[name, office, lat, lon, zone]`; the list position is the `point` u16 | Forecast labels, "forecast for my location" (nearest point by distance) |
| `places.json` | 1.4 MB | `places`: list `[NAME, ST, lat, lon, population]` | Place search and autocomplete. Show a place by the label rule in 9.1 |
| `zips.json` | 1.1 MB | `version` (1), `source`, and `zips`: list `["78701", 30.2706, -97.7426, 29645]` sorted by ZIP: the ZIP as a 5-character string (leading zeros kept, `00901`), the ZCTA's internal point (lat, lon, 4 decimals), and the index into `places.json` `places` of the nearest place by great circle. US Census Bureau 2020 ZCTA Gazetteer (public domain), 33,144 ZIPs including Puerto Rico. ZCTAs approximate delivery ZIPs: PO-box-only and some business ZIPs have no entry and are unknown ZIPs | ZIP search. Take the first 5 digits (ZIP+4 `78701-1234` too) and look them up exactly, never by prefix. Label: the place's label (9.1), a space and the ZIP: `San Juan, PR 00901`, `Hell's Kitchen, NY 10019`. Then the point is a coordinate like any other: nearest `pfm_points` entry, nearest station, zone and county from the polygons. The bot resolves `wx 78701` and `>f 78701` from this same table |
| `zones.json` | 355 KB | Zone id (`TXZ192`) → name, office, state, lat, lon | Naming the areas of a warning; zone lookup for a place |
| `zones.geojson` | 10 MB | Zone polygons (`code` property, e.g. `TXZ192`) | Filling a zone-based warning on the map. Optional download; the app can fall back to the zone centroid pin |
| `counties.json` | 227 KB | County UGC (`TXC453`) → name, state, representative lat/lon | Naming the counties of a storm-based warning; centroid pin |
| `counties.geojson` | 4.8 MB | County polygons (Census cartographic boundaries, 1:5M, `code`/`name`/`state` properties), Polygon or MultiPolygon | Filling counties on the map when a warning has no polygon, and as the outline under one. Optional download like `zones.geojson` |
| `wfos.json` | 9 KB | Office code → `states`, `lat`, `lon`, `zone_count`, in `index.json` `offices` order. `NHC` and `WNS` have no states or zones and also carry a `name` | Office names, `>afd` picker (skip entries with no states) |
| `weather_dict.json`, `regions.json`, `state_index.json` | | Legacy (v3/v4). Not used by v5; `state_index.json` is the same list as `index.json` `states` | |

Area runs decode to UGC codes: state code from `index.json` `states`,
then `C` or `Z`, then the 3-digit number. `TXC453` is in `counties.json`,
`TXZ192` in `zones.json`; both have polygons in the matching GeoJSON.
Louisiana parishes, Alaska boroughs and Virginia's independent cities are
all "counties" here, as in the NWS products.

Bundle versioning: `protocol.json` `version` (8 for v5.0) and `index.json`
`version` (2 since revision 3). Revision 3 changed two bundle files:
`index.json` (`NHC` and `WNS` appended to `offices`, `version` 1 to 2) and
`wfos.json` (the two matching entries). The bot's advert does not carry a
version; a bump is announced in the repository.

### 9.1 Place labels

A `places.json` entry is shown as `Name, ST`; a ZIP as its place's label,
a space and the ZIP. The bot builds every place and town name in its text
replies this way (`meshcore_weather/geodata/names.py`), so an app that
follows these steps shows the same characters for the same place.

1. **Suffixes.** Upper-case the name. Then, in this order, if the name
   ends with the suffix, remove it and any spaces and commas left at the
   end; each suffix at most once: ` CITY (BALANCE)`, ` (BALANCE)`,
   ` (HISTORICAL)`, ` (VILLAGE)`, ` CONSOLIDATED GOVERNMENT`,
   ` METROPOLITAN GOVERNMENT`, ` METRO GOVERNMENT`, ` UNIFIED GOVERNMENT`,
   ` URBAN COUNTY`, ` METRO TOWNSHIP`, ` ZONA URBANA`, ` COMUNIDAD`,
   ` COLONIA`, ` MUNICIPIO`, ` CDP`, ` CITY AND`, ` URBAN`. They are
   Census legal and statistical forms nobody says.
2. **Words.** A word is a run of letters (Unicode category L), decimal
   digits (Nd) and the marks `'` `’` `‘` `ʻ` `` ` `` (apostrophes, the
   Hawaiian ʻokina and its stand-ins). Any other character (space, `-`,
   `/`, `.`, `,`, parentheses) is kept as it is between words.
3. **Case**, word by word:
   - an initialism stays in capitals: `AFB AAF ARB ANGB NAS NAF NOLF MCAS
     USCG MCBH WMATA DC NE NW SE SW VA UC KC II III`;
   - a joining word that is not the name's first word is lower case:
     `OF THE IN ON AT BY AND OR DE DEL DU`;
   - any other word is lower case except its first character, the letter
     after a leading `MC`, and a letter right after a mark that begins the
     word or follows its one-letter start.

   Case changes are each code point's full Unicode mapping.

State codes are not kept in capitals: in place names `LA`, `DE`, `IN`,
`HI` and `OR` are words (La Grange, De Queen, Valley Hi); `DC` is listed
as an initialism. Spanish and French articles keep their capital (Bayou
La Batre, East Los Angeles); joining words do not (Lake of the Woods,
Estancias de Florida, Fond du Lac), except as the first word (De Queen,
Del Rio).

| Bundle entry | Label |
|---|---|
| `HELL'S KITCHEN`, NY, ZIP 10019 | Hell's Kitchen, NY 10019 |
| `CENTRAL 14TH STREET / SPRING ROAD`, DC | Central 14th Street / Spring Road, DC |
| `MCGUIRE AFB`, NJ, ZIP 08562 | McGuire AFB, NJ 08562 |
| `ADJUNTAS ZONA URBANA`, PR, ZIP 00601 | Adjuntas, PR 00601 |
| `ESTANCIAS DE FLORIDA COMUNIDAD`, PR | Estancias de Florida, PR |
| `‘EWA GENTRY`, HI | ‘Ewa Gentry, HI |
| `O'FALLON`, IL | O'Fallon, IL |
| `NASHVILLE-DAVIDSON METROPOLITAN GOVERNMENT (BALANCE)`, TN | Nashville-Davidson, TN |

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
Wind: "WNW 15 gusting 26". Pressure: `29.00 + pressure/100` inHg. Leave
out a field sent as unknown rather than showing its sentinel, and show
visibility 0 as "under 1 mi". A station is "stale" when `ts` is older
than 2 hours. A forecast is stale after 12 hours from `issued`.

### 10.4 Text fallback for people

The bot's text commands (send on `#meshwx` or by DM). Replies come by DM;
a sender the bot cannot DM gets one reply on the channel instead. Long
replies are paged with "(1/3) more"; send `more` for the next page. A DM
reply fits in 156 bytes of UTF-8, "(1/3) more" included.

How the bot answers requests by DM:

- A reply leaves about 2 s after the request arrived, so the bot's own
  ACK of the request clears the first repeater first.
- Replies to one person go out one at a time, in order. The next one
  starts when the phone has acknowledged the one before, or when the bot
  has given up on it.
- A reply keeps one timestamp and one text for all its tries, so a phone
  that already has it hides the repeats.
- A phone that hears no ACK sends the DM again. The bot takes it as the
  same request when the timestamp and the text are the same, within 30
  minutes, or when the text is the same within 2 minutes of the first
  copy. Case and extra spaces do not count. A copy gets nothing when its
  reply was acknowledged or is still going out. It gets the same reply
  again when that reply was never acknowledged, and an answer when the
  rate limit had dropped the request. After 2 minutes the same text is a
  new request.
- `more` is the exception, because people send it again on purpose. A
  `more` with the timestamp of an earlier one is a copy. A `more` with a
  new timestamp is a new request once the page before it (for the first
  `more`, page 1) was acknowledged; while that page is still going out, or
  was never acknowledged, it is a copy.
- `more` sends the first page your phone has not acknowledged. It may
  repeat a page whose ACK was lost; it never skips one. "That was the
  whole reply" comes only after the last page was acknowledged.

```
wx <city ST|ZIP|ST>       conditions, today's high/low, warnings (a state or nothing: overview)
forecast <city ST|ZIP>    next days, one line per day
warn <city ST|ZIP|ST>     active watches, warnings, advisories
storm <ST|city ST>        storm reports, last 6 hours
rain <ST|city ST>         rainfall totals
metar <ICAO|city ST|ZIP>  latest airport observation
taf <ICAO|city ST|ZIP>    terminal aerodrome forecast
outlook <city ST|ZIP>     hazardous weather outlook
space                 space weather
sat                   satellite receiver: lock, signal, drops, newest EMWIN
more                  next page of the last long reply
help                  the command list
```

An app can expose this as a "message the bot" screen for anything the
binary path does not cover.

## 11. Search and place resolution

- **Places**: `places.json` entries are `[NAME, ST, lat, lon, population]`.
  Match by prefix on the name, then rank by distance to the user (or the
  bot), then by population. Always show the state; 207 names appear in
  more than one state. Round Rock exists in TX and AZ. Label a result as in
  section 9.1.
- **ZIPs**: 5 digits (or ZIP+4) is an exact `zips.json` lookup, then a
  coordinate like any other (section 9). Not in the table: unknown ZIP.
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

- A bot is any advert whose name starts with `WX-`. Show the ones you have
  heard and let the user pick. The advert is meant to carry the bot's
  position, but this bot never sets the firmware's advert location policy,
  so positions arrive as 0,0 and cannot be used to rank bots by distance.
- Every message carries `bot` (two bytes of the public key). Keep
  separate state per bot; when two bots cover the same place you may
  hear the same warning identity from both, and it is the same warning.
- Send requests to the bot you selected. How many bots answer depends on
  how the request was sent, not on what it names:
  - **As a DM**: only the addressed bot can decrypt it, so only that bot
    answers. This is the normal path for an app, and it is how you choose
    which bot serves you.
  - **As channel text on `#meshwx`**: every bot on the channel decrypts
    it, and each one that can serve it answers. Expect duplicates where
    coverage overlaps.

  There is no coordination between bots and no nearest-bot suppression in
  either case. Dedupe on `(bot, seq)` and on warning identity.

## 13. Airtime etiquette

- Never poll. Request on user action, and at most once per 5 seconds.
- Wait 15 s for an answer before you ask again. A `>` request sent again
  by DM (same text within 2 minutes, or same timestamp within 30 minutes)
  is answered again only when the last answer finished going out at least
  12 s earlier; a quicker repeat gets nothing.
- Prefer the digest over `>w` when you only need to know what is active.
- Do not re-request something you already hold. The bot has no cache: it
  rebuilds and re-transmits the whole answer, spending airtime for
  everyone on the mesh.
- Listen passively: the scheduled broadcasts (warnings on change, digest
  every 3 h, observations hourly, home forecast every 6 h) cover the
  common case without any request.

## 14. Build checklist

1. Add `#meshwx`; confirm firmware ≥ 1.15 on the radio.
2. Decode `GRP_DATA` with `data_type 0xFF10`; run the test vectors.
3. Track `(bot, seq)`; dedupe; detect gaps → `>d`.
4. Warnings keyed by `(event, office, etn)`; apply Cancel and Digest (mind a full digest, section 5).
5. Render from the bundle tables; never from strings on the wire. Hide unknown fields.
6. Requests with `>`, by DM to pick one bot or as channel text; 15 s timeout, one retry.
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

## 16. Changes in revision 3

Revision 2 described behaviour the bot did not have in several places,
and left some rules unstated. Revision 3 fixes the bot and states the
rules:

| Section | Revision 2 | Revision 3 |
|---|---|---|
| 2.2, 2.3, 8.1 | `seq` was taken when a message was built, so numbers were skipped (a message not sent, a text retried shorter, a failed send), an answer could interleave with a broadcast out of order, and a restart started again from the clock | `seq` is assigned at transmit from one counter for broadcasts and answers, used only by packets the radio accepted, in order, one batch at a time, and saved across restarts. A resend is byte-identical, same `seq`. A text reply's `group` is its first chunk's transmitted `seq` |
| 3 | Only an expiry moved by 30 minutes or more was sent, so an extension from 21:00 to 21:25 never reached the app | Any change to a real expiry is sent; only an invented one (until further notice) counts in 30-minute steps |
| 3 | (silent) | A new `TO.W`, `SV.W`, `FF.W` or `EW.W` warning is sent a second time about two minutes later, under a new `seq` |
| 3, 9 | Products from the National Hurricane Center and the Storm Prediction Center were sent as office 0 (ABQ) | `NHC` (125) and `WNS` (126) are appended to `index.json` `offices`; a product from any other unlisted office is not sent |
| 4 | Implied the flags nibble tells cancelled, expired early and upgraded apart | This bot always sends 0 |
| 5 | (silent) | Entries are sorted by expiry; with `count` 25 the list may be cut, so keep held identities expiring at or after the last entry |
| 6 | Missing groups were sent as invented values (wind 0, visibility 10, pressure 29.92, a variable wind as north); 1/2SM was sent as 1 and M1/4SM or 1 1/2SM as 10; one pressure outside 29.00 to 31.54 inHg lost the whole batch | Missing groups are sent as unknown; visibility is whole miles rounded down; an out-of-range pressure is unknown; a report may be up to 120 minutes older than `ts` |
| 7 | `first` counted from the first day with enough data, so an evening issuance labelled tomorrow as today | `first` counts from the local date of `issued` at the point |
| 7 | Said wintry meant snow, sleet or freezing rain, windy 20 mph sustained, and fog included haze; the bot also set wintry for any temperature of 36 °F or lower | Wintry is set only for wintry precipitation (a frosty night no longer sets it); the spec states the bot's rules: windy at 30 mph sustained or 40 mph gusts, fog without haze |
| 7, 8.2 | `>f <index>` could come back under another index with the same coordinates | It carries the index asked for |
| 8.2 | "At most 60 answered requests per hour", checked after the answer was built | 60 answer packets per hour, checked before building; a `>` DM also passes the people's text limiter (40 per sender, 400 per hour), a channel line does not |
| 8.2, 8.3 | `>metar ICAO` and `>taf ICAO` could answer with a neighbouring station's report or a "no METAR" sentence | A named station gets its own report, starting `METAR <ICAO>` or `TAF <ICAO>`, or Not available reason 0 |
| 8.2, 10.4 | (none) | `>sat` and the text command `sat` report the bot's GOES receiver as one line, Text subject 8 |
| 8.2 | Said coverage filtered only the bare `>w` and `>o` | The bare `>d` is filtered too |
| 10.4, 13 | (silent). Every DM was a new request: a phone's resend was answered again, and resent `more`s skipped pages or ended a reply early | A DM reply leaves about 2 s after the request, one at a time per person, with one timestamp for all its tries, in 156 bytes. A resend of a request (same timestamp and text within 30 minutes, or same text within 2 minutes) gets no second answer, only the same reply again if it was never acknowledged. `more` with a new timestamp is a new request once the page before it was acknowledged, and sends the first page not acknowledged. A `>` request sent again by DM is answered again only 12 s after the last answer went out |
| 9 | Pointed at the top-level `text_subjects` and `not_available_reasons` in `protocol.json` | Use the tables under `v5`; the top-level ones are v4 |
| 8.2, 9, 10.4 | (none) | A US ZIP (`78701`, or ZIP+4 `78701-1234`) works wherever a place does: people's commands, `>f`, `>metar`, `>taf`, `>hwo`. For `>f`, 5 digits is a ZIP and 1-4 digits a point index. New bundle file `zips.json` (Census 2020 ZCTAs): the bot and the app resolve a ZIP from the same table. Section 9.1 states the label rule for a place and a ZIP (`Hell's Kitchen, NY 10019`), which the bot's replies follow; it used to send `Hell'S Kitchen` |

The wire layout did not change, so a revision 2 decoder decodes every
revision 3 packet. Add the bundle's `zips.json`, update `index.json` and `wfos.json`, apply
the digest rule in section 5, hide unknown observation fields, and label
forecast days from `issued` and `first`.

## 17. Corrections in revision 2

Revision 1, the 15 Sep 2026 kit, described behaviour the bot does not
have. Checked against the running bot, commit 67adefc:

| Section | Revision 1 said | The bot actually |
|---|---|---|
| 1, 12 | The advert carries the bot's position | Sends 0,0. It never sets the firmware's advert location policy |
| 1, 8.2 | Requests are DMs | Accepts `>` as a DM or as channel text on `#meshwx` |
| 6 | Observations speed up to every 30 minutes in severe weather | Runs on a fixed hourly interval |
| 6 | (silent) | The station list is recomputed per batch and changes through the day |
| 7 | Forecasts alternate day and night periods | Sends whole days, each with a high and a low, `first` always even |
| 8.2, 13 | An identical request within 5 minutes returns a cached answer | Has no cache; every answer is rebuilt |
| 8.2 | (silent) | Place-named requests are served nationwide; coverage filters only broadcasts and the bare `>w` and `>o` |
| 8.3 | Reason 4 means rate limited | Reason 4 is never sent; throttled requests get silence |
| 12 | Only the nearest bot answers a request naming a place | A DM is answered only by the addressed bot; channel text may be answered by every bot that hears it |

Nothing on the wire changed between revision 1 and revision 2. Only the
description did, so a revision 1 client keeps working.
