# MeshWX v5: the weather protocol for MeshCore apps

Version 5.0, revision 11, 2026-09-20. This is the document an app developer
builds against. It replaces the v3/v4 protocol documents, the April 2026
iOS brief and the v4 client guide, all of which are now superseded.

Revision 11 adds one message: **Radar** (type 11, section 7D), one tile of
a radar picture as a quadtree of four precipitation levels, always one
packet. The pictures are the Weather Service's radar mosaics, which the
EMWIN satellite stream already delivers to the bot's dish every 15
minutes; no internet is involved. It answers the new request `>radar`, is
never broadcast on a schedule, and its refusals carry the letter `x`, the
one request whose Not-available letter is not its first. Nothing already
on the wire changed and unknown types are ignored, so a revision 10 client
keeps working untouched. If you hold revision 10, read section 16.

Revision 10 adds three request forms and reinterprets one byte. `>part`
(section 7C) asks for the packets of a multi-packet answer a phone did not
hear, instead of the whole answer again. `>wmap TXOK` scopes an Area sweep
to the states a person asked for, which the sweep then names in its own
entries, and `total` bit 7 says a sweep is scoped. `>f 35.687,-105.938`
asks for a forecast at a coordinate, so a phone no longer has to know of a
bundled point to ask. The one byte that changed meaning is the sweep's
`total`, and it could change because no revision 9 client had shipped to
anyone. If you hold revision 9, read section 16A.

Revision 9 adds one message: **Area sweep** (type 10, section 7C), the
national picture of active alerts as runs of UGC numbers, which the phone
draws on the zone and county outlines it already ships. It answers the new
requests `>wmap` and `>wmap all`, is never broadcast on a schedule, and is
limited to one sweep every 5 minutes across all senders. Nothing already
on the wire changed and unknown types are ignored, so a revision 8 client
keeps working untouched. If you hold revision 8, read section 16B.

Revision 8 changes one answer and no byte. `>o KAUS` for a station with
no fresh report now answers with the nearest station within 40 km of it
that has one, under that station's own index (section 6), the way a typed
`wx` request always has. An app names the station nearest a place from
its bundled list, which cannot know which stations actually report.

Revision 7 adds two small things and moves no byte. A **data source** in
the flags nibble (section 2.2.1) says where the weather in a message came
from — the bot's own GOES dish, the internet, or both — so a phone can
stop guessing whether it is reading satellite data. And a **cut flag** on
Text (section 8.1) says a narrative was longer than the air allows and the
tail was dropped; the text now ends at a sentence rather than mid-word.
A revision 6 client ignores both bits and reads every packet exactly as
before. If you hold revision 6, read section 16D.

Revision 5 adds two times, so that a phone can say *when* a number is true
instead of implying it is true now: a **per-station age** in Observations
(section 6) and an **issue time** on Warnings (section 3). Both are
appended after everything a revision 4 decoder reads and both are
announced by a bit in the flags nibble, so a revision 4 client keeps
decoding every message exactly as before and simply never learns the two
times. If you hold revision 4, read section 16F.

Revision 4 adds one message: **Coverage** (type 8, section 7A), the bot's
own statement of what it carries — centre, radius, NWS offices, and the
zones it covers as UGC runs. It is broadcast every 3 hours, answers the new
request `>cov`, and reads as the text command `cov`. Nothing already on the
wire changed and unknown types are ignored, so a revision 3 client keeps
working untouched; it simply never learns what the bot covers. If you hold
revision 3, read section 16.1.

Revision 3 fixes bot behaviour that disagreed with revision 2 and states
rules revision 2 left out: when `seq` is assigned, how a full digest may be
cut, unknown observation fields, what `first` counts from, the request
limits, named-station METAR and TAF replies, `>f <index>`, and two offices
appended to the bundle. It also adds `>sat`, and US ZIP codes as places
with the bundle file `zips.json`. The wire layout did not change. If you
hold revision 2, read section 16.2 first.

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
   observations, forecasts and, on request, radar tiles, as MeshCore
   `GRP_DATA` packets on the channel `#meshwx`. Apps decode them with the tables in the preload
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
| 3 | 1 | `type` | High nibble: message type. Low nibble: flags — bits 0 and 1 are type-specific, bits 2 and 3 are the data source (section 2.2.1) in every type but Cancel |

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
| 8 | Coverage | 7A |
| 9 | Request (app → bot, new in revision 6) | 7B |
| 10 | Area sweep (new in revision 9) | 7C |
| 11 | Radar (new in revision 11; reserved until then "for a future structured product") | 7D |
| 12 to 15 | Free for third-party experiments; the bot never sends them | |

Receivers ignore unknown types.

### 2.2.1 Data source (flags bits 2 to 3, new in revision 7)

Where the weather in this message came from. Two bits, `flags & 0x0C`
shifted right by 2, and they mean the same thing in every type that
carries them.

| Value | Name | Meaning |
|---|---|---|
| 0 | unstated | The bot did not say. Every pre-revision-7 bot sends this, and so does any message not built from a weather product |
| 1 | GOES | Received off the GOES satellite by the bot's own dish |
| 2 | internet | Fetched from NOAA over the internet |
| 3 | mixed | Built from products of both kinds |

Which value a message carries depends on what it was built from. A
message rendered from one product — a Warning, a Forecast, a narrative —
states that product's source. A message aggregated from many — a Digest,
an Observations batch — states `mixed` when they did not all arrive the
same way.

Three types always send 0 because there is no weather product behind
them: **Request** (type 9, which the app sends), **Not available**
(type 7), and **Coverage** (type 8, which describes the bot's own
configuration).

**Cancel (type 2) is the exception and always will be.** Its whole flags
nibble is a reason code (section 4), so bits 2 and 3 of a Cancel are part
of a number an app already reads. Never take a source out of a Cancel and
never put one in: reason 4 is reason 4, not "cancelled, from the internet".

Read 0 as the absence of a claim, never as a claim of absence. A bot that
has not been upgraded sends 0 for everything, so "unstated" is not
evidence that the bot lacks a dish. Do not put it on screen as a
provenance badge on its own; it is useful for telling a user why an
answer is thin, and for a bot operator checking that a dish is actually
feeding the bot.

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
the app replaces by identity either way). Bit 1 = the issue time is present
(below, new in revision 5).

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

Then, if the **flags nibble** bit 1 is set, the issue time, as the last two
bytes of the message (new in revision 5):

| Size | Field | Meaning |
|---|---|---|
| 2 | `issued_before` | u16 LE, minutes between the issuance and `expires`. The issue time is `expires − issued_before`. 65535 means 65535 minutes or more (45.5 days): saturated, so read it as "at least that long ago" |

Three things about that layout, because each is deliberate:

- **The presence bit is in the flags nibble, not in `tags`.** The tag byte
  has no spare bit: 7-6 tornado, 5-4 flood source, 3-2 flood damage, 1
  polygon, 0 areas. The flags nibble had three.
- **It is relative to `expires`, not absolute.** Two bytes instead of four,
  on the one message that is regularly near the packet limit. Unlike a
  time relative to "now" it cannot drift: both ends of the subtraction ride
  in the same packet, so a message drained from an offline queue three
  hours later still decodes to the same instant. A warning is always
  issued before it expires, and no NWS product runs 45 days from issuance
  to expiry — a river flood warning, the longest-lived product, is
  re-issued long before that — so the u16 is enough in practice and
  saturates rather than wrapping when it is not.
- **It comes last.** A decoder written for revision 4 stops after the area
  list and never reads it.

**The issue time is never what gets truncated.** A warning with a large
polygon can reach the packet limit, and the bot then sheds detail in the
order a phone can best do without: 30 vertices and 30 runs, then the
polygon thinned to 16 vertices, then 12 runs, then 10 vertices and 6 runs,
then 8 vertices and no runs, and last the runs alone with no polygon. The
two issue-time bytes are not in that list. They cost less than one vertex
(4 bytes), and the line the phone draws — *issued 1:29 PM* — is worth more
than the shape of the eighteenth corner.

The time is the **product's own issuance** — the header time of the NWS
product that created the event, kept across continuations, so an SVS
update does not restamp a warning as newly issued. It is not when the bot
received the file and not when your phone heard the packet. That is the
whole point: a radio out of range for three hours must still say *issued
1:29 PM*.

Storm-based warnings (tornado, severe thunderstorm, flash flood) carry the
polygon and usually a county list. Zone-based products (winter, heat,
wind, fire) carry the zone list only. Draw the polygon when present and
name the counties under it; otherwise fill the listed zones or counties
from `zones.geojson` / `counties.geojson`.

Typical size: a severe thunderstorm warning with 6 vertices and 2
counties is 15 + 27 + 9 = 51 bytes, or 53 with the issue time.

## 4. Cancel (type 2)

Sent once when a warning the bot has sent stops being active (VTEC action
CAN, EXP or UPG) more than 5 minutes before its stored expiry. Flags
nibble: 0 cancelled, 1 expired early, 2 upgraded (a new warning with the
replacement follows). This bot always sends 0, whatever the reason, so do
not wait for a replacement.

The reason is the **whole** nibble, all four bits, and that is why Cancel
is the one type with no data source (section 2.2.1): bits 2 and 3 here
are part of the reason code, not a source. Reason 4 is reason 4. Values 3
to 15 are undefined today; treat any of them as "ended", which is the
part of a Cancel that matters.

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
120 minutes, nearest first, at most 14 (13 when the batch carries the ages
below). Stations drop out when they stop reporting, so do not treat the
batch as a stable description of what the bot covers.

Flags nibble: bit 0 = per-station ages present (new in revision 5).

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 4 | `ts` | u32 Unix minutes of the newest report in the batch, by the report's own `DDHHMMZ` group — not when the bot's feed received the collective it came in, which is one time for every station in it. Each station's report may be up to 120 minutes older than `ts`; with flag bit 0 set, each station says by how much |
| 8 | 1 | `n` | Station count, 1 to 14 (1 to 13 with the ages) |
| 9 | 11 × n | stations | Below |
| 9 + 11 × n | ceil(n / 2) | ages | Only when flags bit 0 is set. Below |

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
When the named station has no report fresh enough to send (120 minutes),
the bot answers with the **nearest station within 40 km of it that has
one**: still `n = 1`, under the index of the station that reported, never
the one asked for (revision 8). A phone that asked for Wright-Patterson
AFB (KFFO), which never reports on the feed, gets Dayton International
(KDAY, 16.6 km away) and files it under KDAY. Not available (reason 0)
only when nothing within 40 km reports. A phone matching the answer to
its request accepts a single station within 40 km of the one it named
from the bot it asked.

### 6.1 Per-station ages (flags bit 0, new in revision 5)

`ts` is one time for the whole batch — the newest report in it — while a
station in the same batch may have filed its METAR up to 120 minutes
earlier. A phone drawing "as of 8:24 PM" under every temperature was
therefore wrong about most of them, and a station whose reading was two
hours stale did not even look stale until two hours after 8:24. The ages
fix that: each station says how far behind `ts` its **own** report is.

The block is `ceil(n / 2)` bytes, one nibble per station, in station
order:

| Station index `i` | Nibble |
|---|---|
| even | low nibble of byte `i / 2` |
| odd | high nibble of byte `i / 2` |

A nibble is the age in **10-minute steps**: 0 to 15, meaning 0 to 150
minutes, which covers the 120-minute admission window with room to spare.
15 means 150 minutes or more. With an odd `n` the last byte's high nibble
is 0 padding. A station's report time is `ts − age`.

The step is a rounding, half up, so a reading is never presented as more
than 4 minutes fresher than it is, and never more than 5 minutes older.
The newest station in the batch always reads 0, because `ts` is its own
time.

**Why a nibble, and why 13 stations.** A batch of 14 stations is already
9 + 11 × 14 = **163 bytes**, two under the limit. A byte of minutes per
station would need 14 more (177); nibbles need 7 (170). Neither fits, so
the ages cost the fourteenth station: a full batch with ages is
9 + 11 × 13 + 7 = **159 bytes**. The bot drops the *farthest* station,
since the list is nearest first, and never the ages — a batch that told
the truth about some stations and left the rest to be guessed at would be
worse than one that says nothing.

The ages are all or nothing: flag bit 0 set means every station in the
batch has one. A decoder written for revision 4 stops after the station
records and never sees the block, which is why it is at the end.

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

## 7A. Coverage (type 8)

What this bot carries, stated by the bot: the centre of its area, how far
it reaches, the NWS offices inside it, and the public forecast zones as UGC
runs. Broadcast every 3 hours and answered on request (`>cov`). New in
revision 4; it is numbered 7A so that no section already referenced by a
client moved.

**Do not infer a bot's coverage from anything else.** An app that guessed
it from the positions of the stations in the hourly Observations and from
the offices of whatever warnings happened to be active told a real phone
that "WX-AUS may not carry alerts for Travis County (NWS Austin/San
Antonio)" — the bot's own home county — because the one warning active at
that moment came from a neighbouring office. The station list is recomputed
for every batch (section 6) and warnings come and go; neither describes
coverage. This message does.

Flags nibble: bit 0 = the zone runs were cut, bit 1 = the office list was
cut (see **Truncation** below).

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 3 | `lat` | i24 LE, degrees × 10000. The centre of the coverage circle, which is the bot's home point |
| 7 | 3 | `lon` | i24 LE, degrees × 10000 |
| 10 | 2 | `radius` | u16 LE kilometres. 0 = no circle stated; the area is then whatever the runs list |
| 12 | 1 | `stations` | The most stations one hourly Observations packet can carry (13 for this bot: 14 fit without the per-station ages of revision 5, 13 with them, section 6.1). 0 = this bot broadcasts no observations for its area. A cap, not a count: the batch is rebuilt every hour (section 6), so a count would describe this hour, not the coverage |
| 13 | 1 | `n` | Office count, 0 to 24 |
| 14 | `n` | offices | One u8 each, index into `index.json` `offices`, ascending. Every office whose zones the bot covers, plus any the operator named outright. An office the bundle does not list is left out rather than sent as 0 |
| 14+`n` | 1 | `k` | Zone-run count, 0 to 30 |
| 15+`n` | 4 × `k` | runs | Exactly the runs a Warning's area list uses (section 3): `state` u8 (bit 7 = 1 for a county, 0 for a forecast zone; bits 6-0 = index into `index.json` `states`), `start` u16 LE, `run` u8. The run covers UGC numbers `start` … `start + run − 1` |

WX-AUS is **39 bytes**: 14 fixed, 4 offices (EWX, FWD, HGX, SJT), then 36
zones that sort into 5 runs — TXZ155-160, TXZ170-175, TXZ186-197,
TXZ205-211, TXZ221-225 — inside 120 km of 30.2672, -97.7431, with the
hourly cap of 13 stations. The vector `coverage_wx_aus` in
`meshwx_v5_vectors.json` is that exact packet.

This bot covers public forecast zones, so every run it sends has the county
bit clear. The bit is there because the encoding is the warning's; a bot
configured by county may use it, and a client must read it either way.

**Truncation.** The two caps are chosen so a full list never costs the
other one: 24 offices and 30 runs together are 159 bytes, inside the
packet. A coverage set larger than that is cut, in the spirit of the
warning area truncation in section 3. The bot keeps the runs that account
for the most zones (then puts them back in ascending order) and sets flag
bit 0; it keeps the 24 lowest office indices and sets flag bit 1.

**Read a cut list as incomplete, never as a denial.** With flag bit 0 set,
a zone absent from the runs may still be covered: say "not listed" or
"unknown", never "not covered". With both flags clear the lists are
complete, and a zone that is absent really is outside the bot's area. This
is the whole point of the message: an app may state what a bot covers, and
must not state what it does not.

`n` = 0 and `k` = 0 together mean the operator set no area filter at all:
the bot broadcasts every product its feed carries and no place is outside
it. That is an answer, not an empty message.

`lat` and `lon` 0,0 with `radius` 0 mean the bot did not state a centre
(its area came from states or offices, not a circle); use the runs alone,
exactly as section 1 says to ignore an advert's 0,0 position. A bot that
knows neither a centre nor a single zone does not send this message at all,
and answers `>cov` with Not available, reason 0.

What coverage governs is unchanged (section 8.2): the scheduled broadcasts
and the three argument-free requests `>d`, `>w` and `>o`. A request that
names a place is served nationwide whatever this message says.

## 7B. Request (type 9)

An app's request, **flooded on `#meshwx` as a datagram** instead of sent
as a DM. New in revision 6. It carries the same `>` text as section 8.2;
what changes is the transport, and the reason is in the field record of
16 September (docs/MESHWX_UI.md §3.1.2 in the app repository): a DM rides
one stored route hop by hop and fails silently when that route has gone
stale, so seven requests in six minutes never reached a bot that was on
the air and answering everyone else, while every answer — a flood — got
through. A flood needs no route. It costs one transmission per repeater
in reach, about what a DM costs by its second try, and it arrives in the
one to three seconds a text request takes.

The answer is unchanged: the same messages, flooded on `#meshwx`, to
everyone (section 8.2). Nothing is addressed back to the phone.

Common header (section 2.2), read for a request:

| Field | Meaning in a Request |
|---|---|
| `seq` | The **sender's** counter, one more per new request, repeated on a resend. Informational; the bot keys copies on `ts` below |
| `bot` | The bot asked, as in every message (two bytes of its public key). `0xFFFF` = any bot. A bot ignores a request that names another bot, which is how an app chooses its bot on a shared channel (section 12) |
| `type` | 9; flags nibble 0 (reserved, a bot ignores it) |

Body:

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 6 | `sender` | The first six bytes of the sender's public key, in key order — the prefix a DM identifies the same phone by, so a request by datagram and one by DM from one phone are one sender to the bot's limits and its copy rule |
| 10 | 4 | `ts` | u32 LE, Unix seconds: the request's own time, the app's clock. A resend repeats it; that is what makes it a copy |
| 14 | ≤ 40 | `text` | The request, exactly as section 8.2 writes it, UTF-8, starting with `>`, no terminator. The packet ends where the text ends |

`request_digest` in `meshwx_v5_vectors.json` is `>d` to bot `0x041D` from
sender `01 02 03 04 05 06` at `ts` 1789660000, `seq` 1 — **16 bytes**:

```
01 1D 04 90 01 02 03 04 05 06 60 0B AC 6A 3E 64
```

**Copies.** The bot treats the same `sender` + `ts` + `text` as one
request for 30 minutes, and the same `sender` + `text` within 2 minutes as
one request whatever the `ts` (the DM rule, section 13). A copy is answered
again, from the bot's cache, only once its previous answer finished going
out at least 12 s earlier; a quicker copy gets nothing, so a resend can
never double the airtime of an answer.

**Limits.** A Request datagram meets the app limits of section 8.2 — one
request per sender every 5 s, 60 answer packets an hour across all senders
— and not the limiter for people's text commands, exactly like a `>` line
sent as channel text.

**What the app does (section 13).** Send once. If no answer arrived after
10 s, send the same bytes once more (same `ts`, same `seq`). Never a third
time. There is no acknowledgement for a datagram; the answer is the
acknowledgement. An app whose radio cannot send channel datagrams
(companion firmware before `CMD_SEND_CHANNEL_DATA`, 0x3E) keeps using the
DM of section 8.2.

**A bot that does not implement type 9** ignores it, as section 2.2 says
of every unknown type. Such a bot still answers the DM form, so an app may
fall back to a DM after the second datagram goes unanswered; this bot
implements type 9 and an app talking to it need not.

## 7C. Area sweep (type 10)

The national picture of what is active, as runs of UGC numbers. New in
revision 9; it is numbered 7C so that no section already referenced by a
client moved.

The phone already ships every zone and county outline (`zones.geojson`,
`counties.geojson`, section 9). So the mesh does not carry a map: it
carries numbers, four bytes for anything from a single county to 64 of
them, and the phone fills in its own outlines. Real products run at about
3.6 runs per alert, so the 125 alerts active on a normal afternoon are
roughly 450 runs: `>wmap` (warnings and watches, a third of them) is
around five packets, and `>wmap all` fills the eight-packet ceiling and
comes back cut. The same picture as polygons would be hundreds of packets.

`area_sweep_national_packet0` and `packet1` in `meshwx_v5_vectors.json`
are one cut two-packet sweep; decode both and reassemble them.
`area_sweep_scoped_packet0` is a sweep of two states, one of which has
nothing active (section 7C.1).

**Request only, and never scheduled.** It answers `>wmap` (warnings and
watches), `>wmap all` (advisories as well) and, since revision 10,
`>wmap [all] [states]`, the same sweep of a few states rather than the
country. Like every other answer it is flooded on `#meshwx`, so one
request serves everyone listening — which is exactly why the bot limits it
hard (below).

Flags nibble: bit 0 **cut**, bit 1 **advisories**, bits 2 and 3 the data
source (section 2.2.1).

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 4 | `built` | u32 LE, Unix minutes: when the bot built this sweep. Not a live feed — show it as a picture taken at `built` |
| 8 | 1 | `group` | The same value on every packet of one sweep: the `seq` its first packet went out with, exactly as Text does it (section 8.1) |
| 9 | 1 | `idx` | Packet number, from 0 |
| 10 | 1 | `total` | Bits 0-3: packets in this sweep, 1 to 8. Bit 7: **scoped** (revision 10, below). Bits 4-6 are 0 |
| 11 | ≤152 | `entries` | 4 bytes each, at most 38 per packet, scope entries included |

One entry is one run of consecutive UGC numbers in one state:

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 1 | `event` | The event code Warning uses (`protocol.json` `events`, section 3): the most severe event covering this run |
| 1 | 1 | `state << 1 \| kind` | State index (`index.json` `states`) in the high 7 bits; `kind` in bit 0: 0 = forecast zone (`Z`), 1 = county (`C`) |
| 2 | 2 | u16 LE | bits 0-9 `start`, the UGC number (0 to 1023); bits 10-15 `run − 1`, so the run is 1 to 64 numbers |

The run covers `start` … `start + run − 1`, the same way a Warning's area
run does. **It is not the same record.** A Warning run (section 3) spends
a whole byte on `run` and flags a county in bit 7 of the state byte; a
sweep entry carries an event code that one does not, and pays for it by
capping the run at 64. Decode them with different code.

Reassemble by `(bot, group)` in `idx` order, as with Text. A sweep is at
most 8 packets and so at most 304 entries, scope entries (section 7C.1)
included.

**Ordering.** Entries are sorted most severe first (warning, then watch,
then advisory, by the significance letter of the event code), then by
state, then by `start`. This is what makes a cut sweep useful: what
survives is the worst of it.

**The cut flag (bit 0).** More runs were active than the sweep could hold
(304, less one for each scope entry) and the least severe were dropped. It is set on **every** packet, not only the last, so
a phone that loses a packet still knows it is not holding the whole
picture. Read a cut sweep as incomplete, never as a denial: an area absent
from it may still be under something.

**The advisories flag (bit 1).** The sweep includes advisories and
statements (VTEC significance `Y` and `S`) as well as warnings and
watches. Clear means warnings and watches only, so again an area absent
from it may still hold an advisory — and usually does. Say which kind of
sweep is on screen; the two are not comparable.

**An entry is one event, not every event.** An area under both a tornado
warning and a flood advisory appears once, under the tornado warning. The
sweep is a picture of the worst thing happening in each place, not an
index of everything: for the detail of one area, ask `>w TXC453`.

**Trailing bytes are padding.** A packet ends where its last whole entry
ends. Bytes left over, fewer than four of them, are ignored: a sweep is
not worth throwing away over three of them, and both shipping clients
already read it this way. The reference decoder did not, and was wrong
(revision 10).

### 7C.1 Scoped sweeps (revision 10)

`>wmap` defaulted to the whole country, which is the most expensive answer
on this mesh and is rarely what a person opened a map to see. From
revision 10 a request may name the states it wants: `>wmap TX`,
`>wmap tx, ok`, `>wmap all TXOKLA`. Up to **15** two-letter codes from
`index.json` `states`, run together or separated by spaces or commas, in
any case. No states is the whole country, exactly as in revision 9. An
unknown code is answered Not available, reason 1.

`all` is the advisories level and is matched as a whole token, never as
Alabama plus a stray letter. `ALLA` is four characters, so it is Alabama
and Louisiana. An app should send the compact upper-case form with no
separators, so that 15 states still fit the 40-byte request text.

**`total` bit 7: scoped.** `total & 0x0F` is the packet count, 1 to 8; bit
7 set means this sweep covers only the states its scope entries name. It
is set on **every** packet of a scoped sweep, for the same reason the cut
flag is: a phone that lost packet 0 must still know it is not looking at
the country.

**Scope entries.** A scoped sweep's packet 0 begins with one entry per
state asked for:

| Field | Value |
|---|---|
| `event` | 0. No event has code 0, which is what tells a scope entry from an alert |
| `state << 1 \| kind` | The state index, `kind` 0 (forecast zone) |
| `start` | 0 |
| `run` | 1 |

That is `XXZ000`, the Weather Service's own way of writing "all of state
XX". Scope entries sort before every alert entry and count toward the 38 a
packet holds, so a sweep of 15 states carries 289 alert entries rather
than 304. They ride on packet 0 alone; the other packets carry bit 7 and
nothing else about the scope.

**A state with no alert entries is an answer**, not an omission: nothing
is active there at this level. That is the whole reason the scope is on
the wire. Draw a scoped sweep as a picture of the states it names and of
nothing else: a state outside the scope is unknown, never clear.

**The cooldown is per state.** The bot records, per state, when a sweep
last covered it and whether that sweep carried advisories; a national
sweep covers every state. A request is refused (Not available, reason 4)
when **every** state it names was covered in the last 5 minutes at the
same or a higher level, `all` being higher than plain. A national request
is refused when a national sweep at that level went out in the last 5
minutes: two scoped sweeps do not add up to a picture of the country.

**The budget.** A national sweep still needs 8 packets left in the hour
before the bot starts one, because half a national sweep is a wrong map.
A scoped sweep is small enough to build first and then measure: it needs
its own packet count left, which is usually one or two.

### 7C.2 `>part`: the packets that did not arrive (revision 10)

`>part <group> <idx>[,<idx>…]`, decimal, e.g. `>part 212 1,4,6`. `group`
is the `group` byte of an Area sweep (type 10) or a Text (type 6), and
each `idx` is a packet or chunk number. A phone holding 4 of 7 packets
asks for the other 3 instead of spending a whole sweep again.

The bot keeps the transmitted bytes of its last **8** multi-packet answers
for **10 minutes**, keyed by the `group` they actually went out with. It
answers by sending the named packets again: the same bytes, with a new
`seq` in byte 0 and **nothing else changed**. In particular `group` does
not change, so the packets file under the assembly they belong to. Not
available, request letter `p`, reason 0 when the bot no longer holds that
group or none of the indexes exist in it.

Limits. The per-sender 5 s rule applies and every resent packet comes out
of the hourly 60-packet budget. The sweep cooldown does **not** apply and
is not restarted: a resend is not a new sweep. The same `(group, idx)` is
resent at most once every **30 seconds** whoever asks, so ten phones that
all missed packet 3 cost one packet. A request whose every index is inside
that window is answered with nothing at all; that is not an error and not
a refusal, it is the bot declining to send twice what is already on the
air.

**When to offer it.** Offered, never automatic. Offer it when an assembly
is incomplete, its newest packet arrived at least **15 s** ago (the bot's
own echo resend has had its chance) and its first packet no more than 10
minutes ago. After that the ordinary ask, the whole map or the whole
report, is the only offer left: the bot no longer holds the bytes.

## 7D. Radar (type 11)

One tile of a radar picture: where precipitation is, in four levels, over a
fixed square of the earth. New in revision 11; numbered 7D so that no
section already referenced by a client moved.

**Where the pictures come from.** The EMWIN stream the bot's dish receives
carries the Weather Service's radar mosaics as GIF images: one national
picture and fourteen regional ones (the Plains, both halves of the Rockies
and the Pacific coast, the Mississippi valley, the Great Lakes, the
North-east and South-east, Alaska, Hawaii, Puerto Rico and Guam), a new
one of each every 15 minutes. The bot reads the newest picture that holds
the tile asked for, regional before national, and cuts the tile out of it.
No internet is involved. A bot fed from the internet bundle has no
pictures, because that bundle is text only, and answers Not available,
reason 2.

**Request only, never scheduled, always one packet.** It answers `>radar`
(section 8.2). Like every answer it is flooded on `#meshwx`, so one request
serves everyone listening, and the tiles sit on a fixed lattice precisely
so that a tile one phone asked for is a tile every phone can use.

`radar_tile` in `meshwx_v5_vectors.json` is real: the tile around Dallas cut
from the Southern Plains mosaic of 20 September 2026, 23:38 UTC, with a
squall line across it, 131 bytes. `radar_tile_coarse_partial` exercises both
flags.

### 7D.1 The tile

- `zoom` is 0 to 3. A tile spans `2^(zoom+1)` degrees on each side: 2, 4, 8
  or 16.
- The grid is 32 x 32 cells, so a cell is `span / 32` degrees: 1/16 of a
  degree at zoom 0 (about 7 km north to south), then 1/8, 1/4 and 1/2.
- Tiles sit on a lattice of **half** their span, `step = 2^zoom` degrees,
  so they overlap, and the tile that answers a coordinate is the one whose
  **centre** is the nearest lattice point:

  ```
  centre_lat = floor(lat / step + 0.5) * step      south = centre_lat - step
  centre_lon = floor(lon / step + 0.5) * step      west  = centre_lon - step
  ```

  `floor(x + 0.5)`, not your language's `round`: a tie must fall the same
  way on the bot and on every phone. The place asked about is then never
  closer to an edge than a quarter of the span, 55 km at zoom 0, so there is
  always room to see what is coming. `south` and `west` are whole degrees
  at every zoom. An app computes the same tile to know which answer is its
  own.
- Row 0 is the **northern** row and column 0 the **western** column. Cell
  `(row, col)` covers latitude `south + span - (row + 1) * cell` to
  `south + span - row * cell` and longitude `west + col * cell` to
  `west + (col + 1) * cell`. Cells are square in degrees, so they are
  narrower than they are tall on the ground everywhere but the equator.
- A cell holds a **level**, 2 bits: 0 none, 1 light (20 dBZ and up), 2
  moderate (35 and up), 3 heavy (50 and up). The thresholds are
  `protocol.json` `v5.radar.levels_dbz`. A cell's level is the **strongest**
  echo in it, not the average: a thunderstorm core is smaller than a cell,
  and averaging it away is the one thing this message must not do. A tile
  therefore reads a little wetter than the picture it came from.

### 7D.2 The packet

Flags nibble: bit 0 **coarse**, bit 1 **partial**, bits 2 and 3 the data
source (section 2.2.1; a picture off the dish is 1).

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 4 | 4 | `taken` | u32 LE, Unix minutes: the time printed on the radar picture. Not when the dish received it and not when the bot sent it (below) |
| 8 | 1 | `south` | i8: the tile's southern edge, whole degrees |
| 9 | 2 | `west` | i16 LE: the tile's western edge, whole degrees, -180 to 179 |
| 11 | 1 | `shape` | bits 0-1 `zoom`; bits 2-7 `product`, an index into `v5.radar.products`: which mosaic the tile was cut from |
| 12 | 4 | `bounds` | **Only when the partial flag is set**: `row0`, `row1`, `col0`, `col1`, one byte each, inclusive, in this packet's own grid |
| 12 or 16 | 1 to 153 | `cells` | A quadtree of levels, most significant bit first, zero bits to the end of the last byte |

**The quadtree.** `node(size)`:

- `size` 1: two bits, the level.
- otherwise one bit. `0`: the whole square is one level, and two bits of
  level follow. `1`: the square is split and four nodes follow, north-west,
  north-east, south-west, south-east, each `node(size / 2)`.

The root is `node(32)`, or `node(16)` in a coarse tile. A tile with nothing
on it is the three bits `0 00` and one byte; the whole packet is 13. Reject
a packet whose bits run out before the tree is complete. Bits left over
after it, fewer than eight and all zero, are padding.

**Coarse (bit 0).** The 32 x 32 tree did not fit the 165 bytes, so the bot
sent the same tile as 16 x 16, each cell the highest of the four it
replaces. A coarse tree is at most 75 bytes and always fits, which is what
makes "one packet, always" true. It takes a picture with echo texture
across most of the tile to need it; the squall line in the vector did not.

**Partial (bit 1).** Part of the tile lies outside the radar picture it was
cut from. `bounds` names the rows and columns that are inside; every cell
outside them is **unknown**. Such cells are level 0 on the wire, because
the tree has no fifth level, so a client must read `bounds` before it
reads a 0 as dry. The bot serves a partial tile only when no picture holds
the whole of it and one holds at least a quarter.

**`taken` is the picture's time.** It is printed in the corner of each
picture and the bot reads it from there, because nothing else says it: a
picture is anywhere from 2 to 34 minutes old when the dish receives it.
When the corner cannot be read the bot sends the product's issue time
instead, which runs 2 to 8 minutes after the picture. Nothing on the wire
says which was used: the difference is inside the error of a picture that
is 10 to 25 minutes old by the time anyone looks at it. **Always show
`taken`**, as a clock time and as an age, and never draw a tile as if it
were live. Precipitation moves 10 to 20 km in the time a picture takes to
reach a phone.

**What a tile cannot say.** Level 0 inside the bounds means the picture
shows no echo there. Over open water, behind mountains and between radars
that is not the same as no rain. And echo under the stroke of a warning
polygon is interpolated from its neighbours, because the Weather Service
draws the polygons on the picture; an app draws the polygon itself from the
Warning (section 3), so nothing is lost that matters.

**Which pictures.** `v5.radar.products` lists the fifteen, and the list is
append-only because `product` indexes it. Fourteen are calibrated. Guam
(index 14) is not: its islands are too small to place the picture by, so a
tile there is answered Not available, reason 0, until it is.

### 7D.3 Limits

An answer is one packet, so the hourly budget of 60 packets and the
per-sender 5 s rule (section 8.2) are limit enough, with one addition. The
same tile cut from the same picture is the same bytes, and everyone in
range already received them: asked for again within **5 minutes** it is
answered Not available, letter `x`, reason 4. The window is keyed on
`(south, west, zoom, taken)`, so a newer picture is never held back by an
older one, and a wider tile is a different tile.

The bot answers from a picture at most **60 minutes** old by `taken`. With
nothing newer it answers Not available, reason 0: an old radar picture is
worse than none, because it looks like an answer.

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

Flags nibble bit 0 (**cut**, new in revision 7): the product was longer
than the air allows and the tail was dropped. Bits 2 and 3 are the data
source (section 2.2.1).

Reassemble by `(bot, group)` in `idx` order; show partial text with a
"missing part" marker if a chunk never arrives. From revision 10, ask for
that chunk with `>part <group> <idx>` (section 7C.2) rather than for the
whole reply again: it is one packet instead of eight, and the chunk comes
back under the same `group`.

**The cut flag.** A reply holds at most 8 chunks of 157 bytes, so 1256
bytes of UTF-8 is the ceiling, and a forecast discussion is routinely
longer. When the bot has to drop the tail it trims at the last sentence
boundary that fits — a `.`, `!` or `?` followed by a space — or, failing
that, at the last word boundary. The text is never cut inside a word and
there is no ellipsis: the flag is the signal, and the bytes it would cost
are airtime.

The flag is set on **every chunk** of a cut reply, not only the last one.
A phone that loses the last packet has still been told that what it holds
is an excerpt.

Show a cut reply as an excerpt — the text, then a quiet line saying the
rest did not fit, and a way to ask the bot again for the parts that
matter. Do not show it as a transmission fault or a missing chunk: every
chunk arrived, and asking again gets the same 1256 bytes. `cut` and a
missing `idx` are different things and should not share a marker.

### 8.2 Request grammar (app side)

An app request starts with `>` and may be sent as a **Request datagram**
on `#meshwx` (section 7B, the normal path since revision 6), as a DM to
the bot, or as text on `#meshwx`. Every way the answer comes back on
`#meshwx` as v5 messages, never as a DM. The same commands without `>` are what people
type and get a text DM back.

| Request | Answer |
|---|---|
| `>d` | Digest |
| `>w` | One Warning message per active warning in coverage (at most 6, newest first), then a Digest |
| `>w SV.W.EWX.42` | That one warning (identity as `event.office.etn` with the office's 3-letter code) |
| `>w TXC453` or `>w TXZ192` | Every active warning touching that county or zone (at most 6) |
| `>wmap` | Area sweep (section 7C): every active warning and watch in the country, as runs of UGC numbers, 1 to 8 packets. At most one national sweep every 5 minutes across all senders (section 7C.1: the window is per state); inside that window, and when fewer than 8 packets of the hour's budget remain, Not available reason 4. Never broadcast on a schedule |
| `>wmap all` | The same sweep with advisories and statements included (flags bit 1 set) |
| `>wmap TXOK`, `>wmap tx, ok`, `>wmap all TXOKLA` | The same sweep of those states only (section 7C.1, revision 10): up to 15 two-letter codes, run together or separated by spaces or commas, any case. `total` bit 7 is set and packet 0 names the states in its scope entries. An unknown code, or more than 15, gets Not available reason 1. The 5-minute cooldown is per state |
| `>part 212 1,4,6` | Those packets of Area sweep or Text `group` 212 again (section 7C.2, revision 10), identical but for a fresh `seq`. Not available `p` reason 0 when the bot no longer holds the group or none of the indexes exist; silence when every index asked for went out again in the last 30 s |
| `>wt SV.W.EWX.42` | The warning's narrative as Text, subject 0 |
| `>o` | Observations for the coverage stations |
| `>o KAUS` | Observations, one station: that one, or the nearest within 40 km that reports (section 6, revision 8) |
| `>f` | Forecast for the bot's home point |
| `>f 102` | Forecast for point index 102; `point` is 102 whenever the forecast is at that point's coordinates (section 7) |
| `>f round rock tx` | Forecast for a place the bot resolves (nearest point; `point` may be 0xFFFF) |
| `>f 35.687,-105.938` | Forecast at a coordinate, decimal degrees (revision 10). The same answer a resolved place gets: the nearest point the bot **holds a forecast for**, within 80 km, under that point's bundle index or 0xFFFF when it is not in the bundle. Recognised by the comma between two signed decimals, so a place with a comma in it is still a place. Out of range gets Not available reason 1 |
| `>f 78701`, `>f 78701-1234` | Forecast for a US ZIP, looked up in `zips.json` (section 9) and answered like a place. A ZIP not in the table gets Not available reason 1 |
| `>afd EWX` | Forecast discussion, Text subject 1 |
| `>space` | Space weather summary, Text subject 2 |
| `>metar KAUS` | That station's own METAR from the last 120 minutes, Text subject 5, starting `METAR KAUS`. Never another station's: without one, Not available reason 0, request `m` |
| `>taf KAUS` | That station's own current TAF, Text subject 5, starting `TAF KAUS` (an amendment reads `TAF KAUS AMD ...`). Never another station's: without one, Not available reason 0, request `t` |
| `>metar round rock tx`, `>taf round rock tx`, bare `>metar` / `>taf` | The nearest station with a report (the bot's home without an argument), Text subject 5, labelled with that station and its distance, e.g. `METAR (KGTU 15km) KGTU 151155Z ...` |
| `>storm TX` `>rain TX` `>hwo` | Text, subjects 3, 4, 6 |
| `>radar` | Radar (section 7D, revision 11): the zoom 0 tile around the bot's home point, one packet |
| `>radar 30.270,-97.740` | The zoom 0 tile for that coordinate, decimal degrees, three decimals: the tile whose centre is nearest (section 7D.1). An app sends this form |
| `>radar 30.270,-97.740 z2` | The same at zoom 2. The zoom is its own last token, `z0` to `z3`; anything else after the place is part of the place, so `>radar zion il` is a town. `z4` and up gets Not available reason 1 |
| `>radar round rock tx`, `>radar 78701 z1` | A place or a ZIP the bot resolves, as `>f` does |
| `>sat` | The bot's GOES receiver now, one line, Text subject 8: lock, signal good/fair/poor, packets dropped in the last minute, age of the newest EMWIN file. A receiver that is not reporting is answered as Text saying so. A Not available for it would carry `s`, the letter `>space` and `>storm` use |
| `>cov` | Coverage (section 7A), one packet: the bot's centre, radius, offices and zone runs. It describes the bot, not a place, so coverage never filters it and it is answerable at any time. A bot that knows neither a centre nor a zone answers Not available reason 0, request `c` |

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
the EMWIN satellite feed carries, not policy. Ask `>cov` for what the bot's
coverage actually is, and take the answer from section 7A: never work it
out from the broadcasts you happen to have received.

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
| 4 | 1 | `request`: ASCII code of the request's first letter (`w`, `o`, `f`, `a`, `s`, `r`, `m`, `t`, `h`, `d`, `c`, `p`), or `x` for `>radar` |
| 5 | 1 | `reason`: 0 no data yet, 1 unknown location, 2 unsupported, 3 bot error, 4 rate limited (try later) |

`p` is `>part` (section 7C.2, revision 10), and it carries reason 0 only:
the bot no longer holds that group, or none of the indexes named exist in
it. Either way the packets are gone and the answer is to ask for the whole
thing again, not to ask for the parts again.

`x` is `>radar` (section 7D, revision 11), the one request whose letter is
not its first: `r` was already `>rain`, and a refusal has to say which of
the two it refuses. Its reasons: 0 no picture newer than 60 minutes holds
the tile (or the region is not calibrated, or no picture reaches it), 1 the
place did not resolve or the zoom is not 0 to 3, 2 this bot has no radar
pictures at all, 4 this tile of this picture went out in the last 5
minutes.

`reason` 0 is ambiguous in this bot. It is sent when nothing is active for
a place, when the bot holds no data for it, and when a named station has
no current METAR or TAF. Treat it as "no answer available right now", not
as "try again shortly".

One case of `reason` 0 does pass on its own: a bot that has just restarted
answers every request with it, whatever was asked, until its products are
loaded (seconds to a couple of minutes, and its scheduled broadcasts are
held back for the same stretch). An app cannot tell that case from the
others, so it should do what it would do anyway — show "no data", and ask
again when the user does.

`reason` 4 is sent by two requests and no others: `>wmap` (section 7C),
which is the one answer big enough to be worth refusing out loud rather
than silently, and `>radar` (section 7D.3), where the refusal tells an app
something it can use: the picture it would get is the one that just went
by. Every other limit in this spec replies with nothing at all,
so silence still means either out of range or throttled, and the app
cannot tell which.

---

## 9. The preload bundle (`client_data/`)

Ship these files in the app. Everything the wire refers to by index lives
here; the bot never sends names.

| File | Size | Contents | Used for |
|---|---|---|---|
| `protocol.json` | 16 KB | `version`, `events` (code → `TO.W`), `event_names` (`short`, `long`), `sky_codes`, and under `v5` the message types, flags, `text_subjects`, `not_available_reasons`, tags, limits and sentinels. The top-level `messages`, `data_types`, `text_subjects` and `not_available_reasons` are v4 tables with other numbers: do not use them for v5 | Every decode |
| `index.json` | 17 KB | `offices`: ordered list of office codes (the `office` byte): the 125 WFOs in alphabetical order, then the national centres `NHC` (125, National Hurricane Center) and `WNS` (126, Storm Prediction Center). `stations`: ordered ICAO list (the `station` u16). `states`: ordered state/territory codes (the `state` byte, bits 6-0). Append-only: new entries go at the end, so an index never changes meaning | Warning, digest, observations |
| `stations.json` | 185 KB | ICAO → name, state, lat, lon | Station search, labels, map pins |
| `pfm_points.json` | 104 KB | `version` (2 since revision 10) and `points`: ordered list `[name, office, lat, lon, zone]`; the list position is the `point` u16. Append-only. It was built from one day's products, so it has never been the whole truth about what the bot can forecast: with no point near a place, ask `>f <lat>,<lon>` rather than deciding there is no forecast | Forecast labels, "forecast for my location" (nearest point by distance) |
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

Bundle versioning: `protocol.json` `version` (15 since revision 11),
`index.json` `version` (2 since revision 3) and `pfm_points.json`
`version` (2 since revision 10). Revision 11 changed one bundle file,
`protocol.json`, which gained the Radar type, its two flags, and the
`v5.radar` block: the grid sizes, the zoom range and spans, the dBZ
thresholds and level names, the `shape` masks, the request letter, the
60-minute and 5-minute limits, and the fifteen products with their names,
in wire order. No index moved. Revision 10 changed two bundle files.
`protocol.json` gained the sweep's scope event code, the scoped bit and
the `total` mask, the 15-state limit, the parts cache in seconds and
groups, the 30-second floor on resending one packet, and a note on the
three new request forms. `pfm_points.json` gained the points nine offices
had none of at all (ABQ, AFC, BOU, GUM, HFO, PIH, PPG, PQE, PQW): they are
**appended**, so no index moves and a held forecast still reads under the
index it was filed with. No index moved anywhere else. Revision 9 changed
one bundle file, `protocol.json`, which gained the Area sweep type, its
two flags, its limits and its two record sizes. No index moved, and the outlines the
sweep is drawn with (`zones.geojson`, `counties.geojson`) are files the
bundle already shipped. Revision 8 changed only
`protocol.json`'s notes. Revision 7 changed one bundle
file, `protocol.json`, which gained `v5.source` (the mask, the shift and
the four values), `v5.flags.text.cut`, and a note on the header's flags
nibble. No index moved. Revision 5 changed one bundle
file, `protocol.json`, which gained the two new flag bits, the 13-station
limit with ages, the age step and range, the issue-time range, and the two
record sizes. No index moved. Revision 4 changed one bundle
file, `protocol.json`, which gained the Coverage type, its flags, its two
limits and its record sizes. No index moved, so `index.json`, `wfos.json`
and every other file are untouched and a revision 3 bundle still decodes
everything except the new message. Revision 3 changed two bundle files:
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

When the issue time is present (flags bit 1, section 3), label it
**issued**: "issued 1:29 PM". Never label the arrival of the packet that
way — the mesh may have taken hours to reach the phone, and "received
1:28 AM" presented as the start of a warning is simply false. With the bit
clear the bot did not state an issue time, so say nothing: the phone's own
receipt time is a fact about the radio, not about the weather, and belongs
in a diagnostics screen if anywhere.

### 10.3 Observations and forecasts

Temperatures are whole °F; show feels-like when `feels` is non-zero.
Wind: "WNW 15 gusting 26". Pressure: `29.00 + pressure/100` inHg. Leave
out a field sent as unknown rather than showing its sentinel, and show
visibility 0 as "under 1 mi". A forecast is stale after 12 hours from
`issued`.

Time a station by its own report, not by the batch. With the ages present
(flags bit 0, section 6.1) the line under a station's temperature is **as
of `ts − age`**, and that station is stale when *that* time is more than
2 hours old — a batch arriving now can hold a two-hour-old reading, and
the age is the only thing that says so. Without the ages, the most an app
honestly knows is that every reading is somewhere in the two hours before
`ts`; say "as of" no more precisely than that, and fall back to the old
rule, stale when `ts` itself is over 2 hours old.

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
cov                   what this bot covers: area, offices, stations, alert list
more                  next page of the last long reply
help                  the command list
```

An app can expose this as a "message the bot" screen for anything the
binary path does not cover.

### 10.5 Which time to show

Two words carry the honesty of the whole screen, and they are not
interchangeable:

- **as of** — an observation, from `ts − age` (section 6.1). What that
  station measured, when it measured it.
- **issued** — a warning, from `expires − issued_before` (section 3), and
  a forecast, from `issued` (section 7). When NWS published it.

Neither is ever the time the packet arrived. A phone that has been out of
range shows old data; saying so is the feature.

### 10.6 Radar

- Three colours for three levels, and a legend that says so in words:
  light, moderate, heavy. They are precipitation, not alerts, so do not
  reuse the alert colours for them, and draw alert shapes as outlines over
  a radar tile rather than as fills that hide it.
- Put `taken` on screen with every tile, as a time and as an age. From 30
  minutes say the precipitation has moved; past 2 hours do not draw the tile
  at all.
- Cells outside `bounds` are unknown. Hatch them or grey them; never leave
  them looking dry.
- Say what a tile means for the place in words as well: what is over it,
  how far and which way the nearest precipitation is, and the nearest heavy
  core when that is somewhere else. The bot's own `radar` text reply does
  exactly this.
- Radar sees snow as well as rain, so the word is precipitation.
- Keep tiles by `(south, west, zoom)`, newest `taken` wins, and use a tile
  somebody else asked for whenever it holds the place being shown.

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
- What a bot covers comes from its Coverage message (section 7A), which it
  broadcasts every 3 hours and answers to `>cov`. That is the only sound
  source for it: two bots' areas may overlap, and what you have happened to
  hear from one says nothing about where it stops.
- Every message carries `bot` (two bytes of the public key). Keep
  separate state per bot; when two bots cover the same place you may
  hear the same warning identity from both, and it is the same warning.
- Send requests to the bot you selected. How many bots answer depends on
  how the request was sent, not on what it names:
  - **As a Request datagram (section 7B)**: every bot on the channel
    decrypts it, and only the one its `bot` field names answers (`0xFFFF`
    asks them all). This is the normal path for an app since revision 6,
    and it is how you choose which bot serves you.
  - **As a DM**: only the addressed bot can decrypt it, so only that bot
    answers. Still supported; a route that has gone stale loses it
    silently, which is why the datagram replaced it.
  - **As channel text on `#meshwx`**: every bot on the channel decrypts
    it, and each one that can serve it answers. Expect duplicates where
    coverage overlaps.

  There is no coordination between bots and no nearest-bot suppression in
  either case. Dedupe on `(bot, seq)` and on warning identity.

## 13. Airtime etiquette

- Never poll. Request on user action, and at most once per 5 seconds.
- Wait for an answer before you ask again: 10 s for a Request datagram,
  15 s for a DM. Ask a datagram at most twice, with the same `ts`. A `>`
  request sent again (same text within 2 minutes, or same `ts` within 30
  minutes, by datagram or by DM) is answered again only when the last
  answer finished going out at least 12 s earlier; a quicker repeat gets
  nothing.
- Prefer the digest over `>w` when you only need to know what is active.
- `>wmap` (section 7C) is the most expensive answer on this mesh: up to
  eight packets, for everyone in range. Ask for it when a person opened a
  map, never to refresh one in the background, and use a sweep another
  phone asked for when you hear it. The bot sends at most one every 5
  minutes whoever asks, and answers Not available reason 4 in between.
- **Ask for the states you are showing**, not the country (section 7C.1).
  A sweep of two states is usually one packet; the country is five, or
  eight with advisories. The country is the right ask when a person is
  looking at the country, and a needless one otherwise.
- **Ask for the packets you missed**, not the answer again (section 7C.2).
  `>part 212 1,4,6` is three packets where `>wmap` would be eight, and the
  bot sends the same bytes it sent before, so nobody has to re-draw what
  they already hold. Offer it, do not do it automatically, and never for
  an assembly older than 10 minutes: the bot has let those bytes go.
- **Radar is one packet, and a new picture exists only every 15 minutes**
  (section 7D). Ask when a person asks. Use any tile you hear that holds
  the place on screen, whoever asked for it. Asking again inside 5 minutes
  of the same picture going out is answered Not available reason 4, which
  costs the mesh a packet too, so tell the user when the picture they hold
  was taken instead of inviting them to refresh it.
- Do not re-request something you already hold. Apart from `>part`, the
  bot has no cache: it rebuilds and re-transmits the whole answer,
  spending airtime for everyone on the mesh.
- Listen passively: the scheduled broadcasts (warnings on change, digest
  every 3 h, observations hourly, home forecast every 6 h, coverage every
  3 h) cover the common case without any request.

## 14. Build checklist

1. Add `#meshwx`; confirm firmware ≥ 1.15 on the radio.
2. Decode `GRP_DATA` with `data_type 0xFF10`; run the test vectors.
3. Track `(bot, seq)`; dedupe; detect gaps → `>d`.
4. Warnings keyed by `(event, office, etn)`; apply Cancel and Digest (mind a full digest, section 5).
5. Render from the bundle tables; never from strings on the wire. Hide unknown fields.
6. Requests with `>`, by DM to pick one bot or as channel text; 15 s timeout, one retry.
7. Stale badges from `ts` and the station's own age, `issued`, `expires`,
   `feed_health`. "As of" is per station (section 6.1); "issued" on a
   warning comes from its issue time (section 3), never from when the
   packet arrived (section 10.5).
8. Text fallback screen with the human commands and `more`.
9. Take the bot's area from Coverage (7A), never from the stations or
   warnings you have seen; read a cut list as incomplete, not as a denial.
10. For an alert map, `>wmap` (7C): reassemble by `(bot, group)`, draw
    each run from your own outlines, and honour `cut` and the advisories
    flag before telling a user an area is clear.
11. Scope the ask to the states on screen (7C.1). Read `total` bit 7 and
    the scope entries of packet 0, and shade no state the sweep does not
    name: outside the scope is unknown, not clear.
12. Offer `>part` for an assembly with a gap in it (7C.2), by the 15 s and
    10-minute rule, and file what comes back under the same `group`.
13. Ask `>f <lat>,<lon>` when a place has a coordinate and your bundle has
    no point near it (8.2). "No forecast point nearby" is a fact about the
    bundle, not about the weather.
14. For radar (7D): compute the tile with `floor(x / step + 0.5)`, decode
    the quadtree most significant bit first, read `bounds` before reading a
    0 as dry, show `taken` with every tile, and pair a `>radar` refusal by
    the letter `x`.

## 15. What changed from v4

For anyone who read the April 2026 brief: the data channel is `#meshwx`
(same channel as the text), datagrams are `GRP_DATA` (no name prefix, no
COBS, no v4 frame header), requests are text with a `>` prefix instead of
`WXQ` + hex, the discovery channel and beacon are gone, the headline is no
longer on the wire (names come from the bundle), warnings carry the VTEC
event byte and storm tags, areas are runs of zone or county numbers, and
a cancel and a digest exist. Observations are batched. Message types are
renumbered; nothing from v3/v4 decodes as v5.

## 16. Changes in revision 11

Revision 11 adds one message, one request and one Not-available letter. No
field moved, no index moved, and no existing message changed.

| Section | Revision 10 | Revision 11 |
|---|---|---|
| 2.2 | Type 11 reserved "for a future structured product" | Type 11 is **Radar** |
| 7D | (none) | One tile of a radar picture in one packet: `taken`, `south`, `west`, `shape` (zoom and product), optional `bounds`, then a quadtree of 2-bit levels. Flags bit 0 coarse (16 x 16), bit 1 partial. Tiles on a lattice of half their span; the tile for a coordinate is the one whose centre is nearest |
| 7D.3 | (none) | One packet per answer under the ordinary limits, plus: the same tile of the same picture is refused (reason 4) for 5 minutes, and no picture older than 60 minutes is served |
| 8.2 | (none) | `>radar`, `>radar <lat>,<lon>`, `>radar <place or ZIP>`, each with an optional last token `z0` to `z3` |
| 8.3 | Every request's letter was its first; reason 4 was `>wmap`'s alone | `x` is `>radar`, because `r` is `>rain`. Reason 4 is sent by `>wmap` and `>radar` |
| 9 | `protocol.json` `version` 14 | `version` 15: the type, the two flags and the `v5.radar` block |
| 10.6, 13, 14 | (silent) | How to draw a tile, when to ask for one, and checklist item 14 |
| 16A-16F | (none) | Every older changelog moved down a letter |

Why radar, in the owner's words of 20 September 2026: *can you take a look
at what comes down over the GOES satellite that we could use to provide
some type of radar coverage on the app?* The answer was already arriving.
EMWIN carries the Weather Service's radar mosaics, and the bot had been
ignoring them because it only opened text. Radar had been in the v4 protocol
and was removed on 14 September precisely because it fetched a 4 MB
composite from the internet every 30 seconds; this source needs no internet
at all.

Why one packet. A radar picture is the most tempting thing on this mesh to
spend airtime on, and the one most likely to be asked for by many people at
once, in exactly the weather that loads the mesh. Four levels over 32 x 32
cells as a quadtree put a squall line in 131 bytes and a clear sky in 13.
When a picture is too busy the bot halves the detail rather than send two
packets, so there is no assembly, no missing part and no `>part` for radar.

Why request-only: the owner's decision, *request-only to start*. A tile is
useful to everyone near it, which is an argument for broadcasting the home
tile when it rains, and that may come. It is not in this revision.

Why `x`. Not available names a request by one letter and `>rain` had `r`.
`>storm`, `>space` and `>sat` already share `s`, which has cost nothing
because all three answer with Text; a radar refusal is one an app acts on,
so it gets a letter of its own.

To adopt revision 11: decode type 11 and run the four new vectors; compute
tiles with `floor(x / step + 0.5)`; keep tiles by `(south, west, zoom)` with
the newest `taken`; honour `bounds`; show `taken`. An app that adopts none
of it ignores type 11 like any unknown type and loses nothing it had.

## 16A. Changes in revision 10

Revision 10 adds three request forms and reinterprets one byte. No field
moved, no index moved, and no message body changed.

| Section | Revision 9 | Revision 10 |
|---|---|---|
| 7C | `total` was the packet count, 1 to 8, in a whole byte | `total & 0x0F` is the packet count; bit 7 is **scoped**, set on every packet of a scoped sweep. Bits 4-6 are 0 |
| 7C.1 | `>wmap` was the country or nothing | `>wmap [all] [states]`: up to 15 two-letter codes, run together, spaced or comma separated, any case. Packet 0 begins with one **scope entry** per state (`event` 0, zone, `start` 0, `run` 1, which is `XXZ000`), which sort before every alert entry and count toward the 38 a packet holds. A state named with no alert entries is an answer: nothing is active there at that level |
| 7C.1 | One sweep every 5 minutes across all senders | The same 5 minutes, per state. A request is refused (reason 4) when every state it names was covered that recently at the same or a higher level; a national request when a **national** sweep at that level was. A national sweep still needs 8 packets of budget; a scoped one is built first and needs its own count |
| 7C | A trailing partial entry was undefined; the reference decoder rejected the whole packet, the clients ignored the bytes | Stated: a packet ends at its last whole entry and the leftovers are padding. The reference decoder now agrees with the clients |
| 7C.2, 8.1 | A missing packet meant asking for the whole answer again | `>part <group> <idx>[,<idx>…]`: those packets again, identical but for a fresh `seq`, from the bot's cache of its last 8 multi-packet answers, kept 10 minutes. Same `group`, the per-sender 5 s rule, the hourly budget, no sweep cooldown, and at most one resend of a `(group, idx)` every 30 s whoever asks |
| 8.2 | `>f` took a point index, a place or a ZIP | `>f <lat>,<lon>` as well: decimal degrees, recognised by the comma between two signed decimals, answered exactly as a resolved place is |
| 8.3 | The request letters had no `p` | `p` is `>part`, and carries reason 0 only |
| 9 | `protocol.json` `version` 13 | `version` 14: the scope event code, the scoped bit and the `total` mask, the 15-state limit, the parts cache in seconds and groups, the 30-second resend floor, and the three request forms. `pfm_points.json` `version` 2: points appended for the nine offices that had none, so no index moves |
| 13, 14 | (silent) | Ask for the states on screen, ask for the packets you missed, and ask `>f <lat>,<lon>` when your bundle has no point near a place |
| 16B-16F | (none) | Every older changelog moved down a letter |

**Why the `total` byte could change.** It is the only byte in this spec
whose meaning revision 10 alters, and altering a byte is normally out of
the question. Two things made it safe. Revision 9 wrote all eight bits but
never a value above 8, so every sweep it ever produced reads identically
under `total & 0x0F`: the bytes on the air do not change, only what a
reader is entitled to assume about the four high bits. And no revision 9
client had shipped to anyone when this was written, so there was no phone
in the field to disagree with. There will not be a second chance like it;
after this, the sweep's spare room is bits 4-6 and nothing else.

Why a scope at all, in the owner's words of 20 September 2026: *can we go
from national alert map to just alert map, and have a way for the user to
select which areas they want to request the warnings for. One, a few, or
all. That way we don't default to sending everything.* A national sweep is
five packets, eight with advisories, flooded to everyone in range, for a
person who was looking at one state. The scope is how that person asks for
one state and is told about it honestly, including "nothing is active
here", which a sweep that simply left the state out could never say.

Why `>part`: *"4 of 7 parts arrived" should allow me to re-request the
missing data.* Asking again cost eight packets to recover three, so the
cheapest thing an app could do was give up. The bot already had the bytes
in hand for as long as anyone would want them.

Why `>f <lat>,<lon>`: `pfm_points.json` was built from one day's products
and has no point at all for nine offices, so a phone in Albuquerque was
told there was no forecast point nearby while the bot held a forecast
15 km away. The bundle gains those points, and the app stops depending on
the bundle being complete.

To adopt revision 10: mask `total` with `0x0F` and read bit 7; lift the
leading `event` 0 entries out of packet 0 as the scope and never shade a
state outside it; offer `>part` for an incomplete assembly and file what
comes back under the `group` it carries; and ask `>f <lat>,<lon>` instead
of deciding a place has no forecast. An app that adopts none of them still
reads every national sweep exactly as it did, and still gets an answer to
every request it knows how to send; what it cannot do is read a **scoped**
sweep, whose `total` it would take for 129 packets. It should therefore
not send `>wmap` with states until it has read this section.

## 16B. Changes in revision 9

Revision 9 adds one message: **Area sweep** (type 10, section 7C), the
national picture of active alerts as runs of UGC numbers. Nothing already
on the wire changed and unknown types are ignored, so a revision 8 client
keeps working untouched; it simply never learns what is active outside its
own area.

| Section | Revision 8 | Revision 9 |
|---|---|---|
| 2.2 | Types 10 and 11 reserved | Type 10 is the Area sweep; 11 stays reserved |
| 7C | — | New: Area sweep. `built`, `group`, `idx`, `total`, then 4-byte entries: an event code, `state << 1 \| kind`, and a u16 holding a 10-bit `start` and a 6-bit `run − 1`. Flags bit 0 cut, bit 1 advisories |
| 8.2 | — | New requests `>wmap` and `>wmap all` |
| 8.3 | `reason` 4 defined but never sent | `>wmap` answers with it when a sweep is refused |
| 9 | `protocol.json` `version` 12 | `version` 13: the Area sweep type, its flags, limits and record sizes |
| 13, 16B-16E | (none) | Every older changelog moved down a letter, as it does with each revision: revision 8 is now 16B, revision 7 is 16C, revision 6 is 16D and revision 5 is 16E |

Why: an app can draw the whole country from four bytes per run of
counties, because it already ships the outlines. What it cannot do is
carry the airtime of a map. So the sweep is the smallest thing that is
still a national picture, and the bot spends it carefully.

**The airtime rules are part of the message**, not bot policy an app may
route around:

- It is **never** scheduled. It only ever answers a request.
- At most **one sweep every 5 minutes across all senders**, timed from
  when a sweep actually went out. A request inside that window is answered
  with the 6-byte Not available, reason 4.
- It comes out of the same 60-packet hourly budget as every other answer.
  With fewer than 8 packets of that budget left the bot does not start one
  and answers reason 4, because half a sweep is a wrong map, not a partial
  one.
- The answer is flooded like every other, so **one request serves everyone
  listening**. An app that hears a sweep it did not ask for should use it.

To adopt revision 9: decode type 10, reassemble by `(bot, group)`, and
draw each entry's run from your own zone and county outlines. Read `cut`
and the advisories flag as section 7C says — an area missing from a sweep
is not an area with nothing in it. An app that does not implement type 10
ignores it, as section 2.2 says of every unknown type, and should not send
`>wmap`.

## 16C. Changes in revision 8

Revision 8 changes what one request answers. No byte, field or index
moved.

| Section | Revision 7 | Revision 8 |
|---|---|---|
| 6, 8.2 | `>o KAUS` answered with that station's report or Not available | That station's report, or the nearest station within 40 km of it that has a fresh one, as a batch of one under the reporting station's own index. Not available only when nothing within 40 km reports |
| 9 | `protocol.json` `version` 11 | `version` 12: the note on `>o` |

Why: 309 of the 2,237 bundled stations sent nothing on the feed in a day
(2026-09-18). An app that asks for the station nearest a place from its
bundled list asks, for a place like Dayton, for one that never answers,
while the typed `wx dayton oh` found Dayton International 16 km away. The
bot knows which stations report; now it chooses.

To adopt revision 8: when matching an answer to `>o ICAO`, accept a single
station within 40 km of the one named, from the bot asked. A revision 7
app already stores the reading, since every Observations message is filed
by its own indices; it only fails to count it as the answer.

## 16D. Changes in revision 7

Revision 7 adds two flag bits and moves no byte. Nothing in any body
changed, no field grew, and no index moved.

| Section | Revision 6 | Revision 7 |
|---|---|---|
| 2.2 | The flags nibble was entirely type-specific, and bits 2 and 3 were unused in every type but Cancel | Bits 2 and 3 are the **data source**, the same field in every type but Cancel: 0 unstated, 1 the bot's own GOES dish, 2 the internet, 3 both. See 2.2.1 |
| 2.2.1 | (none) | Which value a message carries: a message rendered from one product states that product's source; one aggregated from many states `mixed` when they disagree. Request, Not available and Coverage state 0 — there is no weather product behind them |
| 4 | The Cancel flags nibble was a reason code | Unchanged, and now stated as the one exception: the reason is all four bits, so a Cancel never carries a source and none may be read out of one. Reason 4 is reason 4 |
| 8.1 | A reply longer than eight chunks was cut at a byte count, landing mid-word, and said nothing about it. A phone could not tell an excerpt from a corrupt one | Flags bit 0 (**cut**) says the tail was dropped. The text is trimmed at the last sentence boundary that fits, or the last word boundary, never inside a word, and carries no ellipsis. The bit is on **every** chunk, so losing the last packet does not lose the fact |
| 8.1 | (silent) | What a receiver shows: an excerpt with a way to ask again, not a transmission fault. `cut` and a missing `idx` are different things |
| 9 | `protocol.json` `version` 10 | `version` 11: `v5.source` (mask, shift, values), `v5.flags.text.cut`, and the header note. `index.json` stays at version 2 |

A revision 6 decoder is unaffected. It masks the flag bits it knows and
ignores the rest, so it reads every revision 7 packet exactly as it read a
revision 6 one — it simply never learns where the data came from, and a
cut reply looks to it like any other reply, as it did before. The vectors
show both: `severe_thunderstorm_warning_issued` is the revision 6 bytes,
unchanged, and `severe_thunderstorm_warning_from_goes` is the same warning
with source 1 in the nibble. `text_afd_cut_from_goes` carries both new
bits at once (flags `0x5`).

To adopt revision 7: read two bits out of the flags nibble everywhere but
Cancel, read bit 0 of a Text chunk, and stop drawing a cut narrative as
damage. Nothing else needs touching.

## 16E. Changes in revision 6

Revision 6 adds one message and changes no existing byte: **Request
(type 9, section 7B)**, an app's `>` request flooded on `#meshwx` as a
datagram. The DM and channel-text forms of section 8.2 still work; a
revision 5 bot ignores type 9 and a revision 5 app never sends it.
Sections 8.2, 12 and 13 say where the datagram fits.

## 16F. Changes in revision 5

Revision 5 adds two times and nothing else. Both are appended after
everything revision 4 reads, both are announced by a flags-nibble bit, and
no field moved.

| Section | Revision 4 | Revision 5 |
|---|---|---|
| 3 | A warning carried `expires` and nothing about when it began, so an app had only its own receipt time and showed "received 1:28 AM" as if the warning had started then — hours wrong whenever the radio had been out of range | Flags nibble bit 1 says the issue time follows, as the last two bytes: `issued_before`, a u16 of the minutes between the issuance and `expires`. It is the product's own issuance, kept from the NEW segment across continuations, never when the bot received the file. Two bytes rather than four because this is the message that reaches the packet limit; it saturates at 65535 minutes instead of wrapping |
| 3 | (silent about what gives way first) | The truncation order is stated, and the issue time is not in it: vertices, then runs, then the polygon entirely. Two bytes never cost a warning its shape, and the shape never costs it its time |
| 6 | A batch carried one `ts`, the newest report in it, while the builder admitted stations up to 120 minutes older. "As of 8:24 PM" under a 6:25 PM reading was the usual case, not the edge case, and that station did not read as stale until 10:24 | Flags nibble bit 0 says per-station ages follow the station records: `ceil(n / 2)` bytes, one nibble each, in 10-minute steps to 150 minutes, station *i* in the low nibble of byte *i* / 2 when *i* is even and the high nibble when odd. A station's report time is `ts − age`, and staleness is measured from that. See 6.1 |
| 6 | Up to 14 stations in a batch, 163 bytes | Still 14 without the ages; **13** with them, because 163 + 7 does not fit in 165. A full batch drops its farthest station — the list is nearest first — and never drops the ages for some stations only |
| 9 | `protocol.json` `version` 9 | `version` 10: `v5.flags.warning.issued`, `v5.flags.observations.ages`, the station limit with ages, the age step and range, the issue-time range and the two record sizes. `index.json` stays at version 2 and no office, station or state index moved |
| 10.2, 10.3, 10.5 | (silent) | What the two words mean and when to use them: **as of** for an observation, from `ts − age`; **issued** for a warning and a forecast, from the product's own time. Neither is ever when the packet arrived |
| 14 | Stale badges from `ts`, `issued`, `expires`, `feed_health` | The same, plus each station's own age |

A revision 4 decoder is unaffected. It stops after the area list of a
warning and after the station records of an observations batch, ignores
flag bits it does not know, and so reads every revision 5 packet exactly
as it read a revision 4 one — it simply never learns the two times. The
vectors show both forms: `severe_thunderstorm_warning_polygon` and
`observations_three_stations` are the old bytes, unchanged to the byte,
and `severe_thunderstorm_warning_issued` (53 bytes) and
`observations_three_stations_ages` (44 bytes) are the same messages as
this bot sends them from revision 5 on.

To adopt revision 5: read the two flag bits, decode the two blocks, and
replace every screen that timestamps weather with the arrival of its
packet. Nothing else needs touching.

### 16.1 Changes in revision 4

Revision 4 adds one message and the two ways to ask for it. Nothing that
was already on the wire changed, and no bundle index moved.

| Section | Revision 3 | Revision 4 |
|---|---|---|
| 2.2 | Types 8 to 11 were reserved for future structured products | Type 8 is **Coverage**, section 7A; 9 to 11 stay reserved |
| 7A | (none) | New message: the bot's coverage centre, the radius in km, the NWS offices it covers, the cap on the hourly observation batch, and its public zones as the same UGC runs a warning's area list uses. 39 bytes for WX-AUS's real coverage. Each list has a cut flag, and a cut list must be read as incomplete, never as a denial |
| 8.2 | An app had to infer the bot's area from the stations and warnings it had heard, which is wrong often enough to put a false line on a phone | `>cov` answers with one Coverage packet. It describes the bot, not a place, so coverage never filters it |
| 8.3 | The request letters had no `c` | `c` is `>cov` |
| 9 | `protocol.json` `version` 8 | `version` 9: `v5.types.coverage`, `v5.flags.coverage`, the two coverage limits and the coverage record sizes. `index.json` stays at version 2 and no office, station or state index moved |
| 10.4 | (none) | The text command `cov` says the same thing in words: the area and how far it reaches, the offices, the hourly station cap, and how often the alert list goes out. It pages with `more` like any long reply |
| 12, 13 | (silent) | A bot's coverage is what it states, not what you have heard from it; the coverage broadcast joins the passive set, one packet every 3 h |

A revision 3 decoder ignores type 8 (section 2.2) and is unaffected: it
keeps decoding every other message exactly as before and simply never
learns what the bot covers. To adopt revision 4, decode section 7A and
replace whatever your app currently guesses about a bot's area with what
the bot states.

### 16.2 Changes in revision 3

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
