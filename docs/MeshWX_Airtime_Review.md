# MeshWX Airtime Review and v5 Proposal

Adversarial review of the MeshWX protocol and the EMWIN → mesh pipeline, written 2026-09-10 against the working tree at commit d8d4aed plus uncommitted changes. Goal set by the operator:

1. Cut LoRa airtime on the Austin mesh to the minimum that still delivers warnings and observations.
2. Work for people who do not run the DigitainoMesh app, through ordinary MeshCore text DMs.
3. Never transmit on the public channel.
4. Be an open, easily implementable protocol for other apps.

Sources: this repo's code and 12 days of container logs; MeshCore firmware source at v1.17.1 (commit 0679dbe, 2026-08-24) and meshcore_py 2.3.9.1. Firmware citations are `file:line` in the upstream repo. Airtime is computed with the RadioLib formula at the US recommended preset (910.525 MHz, SF7, BW 62.5 kHz, CR 4/5, 32-symbol preamble).

---

## 1. What MeshCore actually does with your bytes

These are the facts that shape every decision below. Most of them were not known when v3/v4 were designed.

### 1.1 Packet on the air

```
[header 1][path_len 1][path 0..64][payload ≤184]      max 250 bytes
```

Header bits: route type (flood / direct), payload type, payload version (`Packet.h:8-17`). Every repeater that forwards a flood appends its 1-byte pubkey prefix to the path, so a packet grows by one byte per hop.

### 1.2 Flood vs direct, and what each costs

- **Channel messages are always flood.** `sendGroupMessage()` ends in `sendFlood()` (`BaseChatMesh.cpp:502`); the companion command `CMD_SEND_CHANNEL_TXT_MSG` has no path field. Every repeater within `flood.max` hops that hears it retransmits it once. Cost ≈ (1 + number of repeaters in range) × airtime.
- **Direct packets** are forwarded only by the repeater named first in the path (`Mesh.cpp:78-110`). Cost = (hops + 1) × airtime. Only DMs, ACKs, and paths use this.
- **Repeaters drop duplicates by payload hash.** The seen-table key is SHA256(payload_type ‖ payload)[0:8]; path and route bits are excluded (`Packet.cpp:41-50`, `SimpleMeshTables.h`, 160 slots). Consequence: if you re-send a byte-identical payload while repeaters still remember it, they silently drop the repeat. Every payload must carry something unique.
- **Companion radios never repeat** (`Mesh::allowPacketForward` false; `isRepeatEn()` off by default). Only repeater firmware carries traffic.
- **Repeater duty cycle** defaults to 50 % (`airtime_factor 1.0`, `Dispatcher.cpp:11-53`). The companion radio the bot uses has no cap. A chatty bot can exhaust the repeaters' budget and delay everyone's traffic, not just its own.

### 1.3 The channel text envelope (what the bot uses today)

`GRP_TXT` payload = `[channel hash 1][MAC 2][AES-128-ECB ciphertext, zero-padded to 16]`.
Plaintext = `[timestamp 4][txt_type 1]["<sender name>: " + text]`, clipped to 160 bytes (`BaseChatMesh.cpp:487-506`, `BaseChatMesh.h:8`).

So each binary broadcast today pays: 3 + 5 + len(name)+2 + 0–15 bytes of padding, and the firmware prepends the bot's advert name in cleartext inside the encrypted blob. The receiving firmware treats the text as a C string (`data[len]=0`, then `strlen`, `BaseChatMesh.cpp:395-399`, `companion MyMesh.cpp:564`). That, plus ECB zero padding, is the real source of the "null bytes get truncated" problem. COBS was a correct workaround for a problem the transport no longer requires.

### 1.4 The channel binary envelope (available since companion v1.15.0, 2026-04-19)

`GRP_DATA` (payload type 0x06): same outer layout, plaintext = `[data_type uint16 LE][data_len uint8][data ≤165]` (`payloads.md:242-256`, `MeshCore.h:21`). Explicit length, arbitrary bytes, no sender name, no null-byte issue, no COBS. Companion command `CMD_SEND_CHANNEL_DATA (62)`; inbound frame `RESP_CODE_CHANNEL_DATA_RECV (27)`. `data_type` is a registry (`docs/number_allocations.md`); `0xFF00–0xFFFF` is the development range. meshcore_py 2.3.9.1 parses the inbound frame but has no send helper; a 10-line wrapper around `commands.send()` is needed. Also flood-only via the command, but the command accepts an explicit path for direct delivery.

### 1.5 DMs and ACKs

`TXT_MSG` plaintext = `[timestamp 4][flags 1][text ≤160]`, keyed by ECDH. When a DM arrives by flood, the receiver replies with a **flooded PATH packet** carrying the ACK, which also teaches the sender the return path (`BaseChatMesh.cpp:248-255`). Once a path is known, subsequent DMs and ACKs go direct. Practical cost of one human query to the bot on a mesh with a known path: request (direct, hops+1) + bot's ACK (direct) + reply (direct) + human's ACK (direct). First contact costs two floods.

### 1.6 Airtime at the US preset

| On-air bytes | Time |
|---|---|
| 0-byte payload (fixed cost of any packet) | ~111 ms |
| 53 | ~254 ms |
| 101 | ~398 ms |
| 165 (a full channel text packet) | ~582 ms |
| 250 (max) | ~830 ms |

Roughly 3 ms per byte plus 110 ms per packet. **Per-packet overhead equals about 35 payload bytes.** Small messages should be batched; big messages should be shrunk. Multiply everything by (1 + repeaters) for a flood.

---

## 2. What the current system puts on the air

Measured from the container log, 2026-08-29 → 2026-09-10, Austin bot, live schedule (radar disabled; note TX was disabled for part of this window, so these are *attempted* sends):

| Job | Interval | Runs | Payload bytes | Share |
|---|---|---|---|---|
| warnings_delta | 5 min | 354 | 43,715 | 57 % |
| warnings (full) | 6 h | 47 | 27,995 | 37 % |
| forecast Austin | 3 h | 99 | 2,737 | 4 % |
| observation Austin | 3 h | 100 | 1,600 | 2 % |

Per-message wire sizes measured in the container (after COBS, before the firmware envelope):

| Message | Bytes | On-air packet | Airtime (0 hops) |
|---|---|---|---|
| 0x21 warning, 5 zones + headline | 131 | 165 | 582 ms |
| 0x20 polygon warning, 4 vertices + headline | 126 | 165 | 582 ms |
| 0x31 forecast, 7 periods | 57 | 101 | 398 ms |
| 0x30 observation | 17 | 53 | 254 ms |
| text reply, 136 chars, DM | 136 | 150 | 541 ms |
| WXQ hex request DM | 19 | 38 | 214 ms |

The warning stream's most frequent "CHANGED" identities over the window were `FL.W.LCH.36/37/38` (Lake Charles river floods), `HT.Y.FWD` and `HT.Y.OUN` (heat advisories, Dallas and Norman OK offices), `FL.W.SHV` (Shreveport), and `FA.W/FF.W.HGX` (Houston floods). None of these affect Austin.

---

## 3. Adversarial findings

Ordered by airtime impact. Each has evidence and a concrete consequence.

### F1. Coverage is the entire state of Texas — critical

`.env` sets `MCW_HOME_STATES=TX` plus four WFOs. `Coverage.covers_any()` (`protocol/coverage.py:185-206`) returns true for any UGC whose 2-letter prefix is in `explicit_states`, and `_derive_wfo_states()` adds TX again for each WFO. Every warning touching any Texas zone is flooded across the Austin mesh. This is why 94 % of bytes are warnings and most of them are river floods 300 km away. Fixing this alone removes the majority of airtime.

### F2. The headline text is the single largest byte consumer — critical

`pack_warning_zones()` and `pack_warning_polygon()` append the shortened NWS headline up to the 136-byte cap (`meshwx.py:1163`, `:594`). In the measured 130-byte zone warning, 26 bytes are structured fields and ~104 bytes are ASCII like `SEVERE THUNDERSTORM WARNING til 445 PM CDT for Travis…`. Everything in that string is derivable from fields already present (type, severity, expiry, zone list) plus a client-side name table. The wire carries the same information twice, and the text copy costs 4× the structured copy.

### F3. Delta churn re-sends whole warnings for trivial updates — high

`_warning_fingerprint()` = `(expiry_minute, hash(headline))` (`schedule/executor.py:223-233`). Any SVS/FLS follow-up that extends the expiry by an hour or rewords the headline re-floods the full 130-byte message. River flood warnings are updated every 6–12 h for days. Also, Python's `hash()` on `str` is salted per process, so every container restart makes every active warning look CHANGED. (Tracking state is in memory too, so a restart already re-sends everything as NEW.)

### F4. Full re-broadcast every 6 h regardless of need — medium

`warnings-full` re-sends all active warnings (252 bytes typical, three packets, each flooded) as a loss-recovery mechanism. Nobody asked for them; there is no cheaper "here is what is active" digest and no way for a client to ask for just the one it missed.

### F5. On-demand responses are sent twice — medium

`_V2_RESEND_COUNT = 2` with a 3 s gap (`protocol/broadcaster.py:207-208`, `:373-388`). Each copy is a full flood through every repeater. The firmware timestamp differs between copies so repeaters do not dedupe them. This doubles the cost of every request, for a reliability gain that is not measured.

### F6. Binary is tunnelled inside a text message — medium, and the root cause of several others

`send_binary_channel()` (`meshcore/radio.py:256-290`) builds `CMD_SEND_CHANNEL_TXT_MSG` with `txt_type 0`. Consequences: the bot's advert name (~18–20 bytes) is transmitted inside every packet; the payload is silently clipped at 160 − name (commit 758b17b and 02977cf were symptom fixes for exactly this); COBS is required; clients must split on `": "` and strip the name before decoding; and the useful MTU dropped from 165 to ~137. `GRP_DATA` fixes all five at once.

### F7. Re-sent identical payloads may be dropped by repeaters — latent correctness bug

Repeaters dedupe on payload bytes (§1.2). Today the firmware-inserted timestamp changes per send, which accidentally protects the bot. Once you move to `GRP_DATA` there is no timestamp in the plaintext: a byte-identical re-broadcast (e.g. the 6 h full refresh, or a cached response re-sent within 5 min) will be dropped by any repeater that still has it in its 160-entry seen table. Any new format must carry a per-message sequence byte.

### F8. Docs describe a wire format that is not on the wire — high for the "open protocol" goal

README, `MeshWX_Protocol_v4_Design.md` and `v4_client_guide.md` state that every message on the data channel is v4-framed (6-byte header, sequence numbers). The scheduler only v4-wraps radar and AFD (`scheduler.py:262-266`, `_FEC_PRODUCTS`); the reactive path sends raw v3 (`broadcaster.py:373-380`). A third party implementing from the docs will write a decoder that fails on every warning, observation, and forecast. Sequence numbers, the one v4 feature that matters for a lossy broadcast, are absent from the products that matter.

### F9. Requests are hex-encoded text — low

`WXQ` + hex doubles the request bytes (8 → 19 chars) and the DM envelope pads to 16. Rare, but a hex-in-text request is not something you want in an open spec when the firmware now has a binary path and, more importantly, when plain human-readable text commands could serve both apps and humans (§4.6).

### F10. Text path sends two floods per channel query and floods adverts on unknown senders — medium if used

`_respond_channel()` sends the reply and then a "send an advert" nudge as two separate channel messages (`main.py:486-527`), and `_handle_channel_message()` calls `_send_advert()` (a ~120-byte flood) whenever an unknown sender appears (`main.py:181-184`). Text replies are up to 136 chars with `more` pagination. Since the operator's requirement is no channel traffic for text at all, this path should be DM-only and single-packet.

### F11. County UGCs are not representable — correctness gap that also costs bytes

`_segment_to_entry()` keeps only `...Z...` codes (`protocol/warnings.py:376`). SVR/TOR/FFW are county-based (`TXC453`) and so always fall through to the polygon format (0x20), which is bigger, and any county-only product without a polygon is dropped entirely. `meshwx.py` has no county location type.

### F12. Two builder implementations for the same products — maintenance risk

`broadcaster.py` and `schedule/executor.py` each implement `_build_observation`, `_build_forecast`, etc. The module docstring acknowledges it. Reactive and scheduled paths can disagree on bytes for the same product, and every spec change must be made twice.

### F13. Discovery costs a channel slot on every device and a second bot channel — low airtime, high friction

The ping/beacon scheme is nearly free in airtime but requires every client and bot to burn one of eight channel slots and requires the app to know the protocol. Adverts already carry name and lat/lon (`AdvertDataHelpers.cpp`); a naming convention makes discovery free.

### F14. FEC radar: six full packets per region per cycle — high when enabled

64×64 radar is base + 4 quadrants + parity ≈ 6 × ~130 bytes ≈ 3.5 s of airtime per region at 0 hops, multiplied by repeaters. XOR parity spends a full packet to recover one loss. It is disabled in the live config, which is the right call for a warnings-and-observations mesh. Nothing below tries to make radar cheap; it should stay request-only or off.

### F15. Products that are already fine

Observation (17 B) and 7-period forecast (57 B) are compact. Their cost is dominated by per-packet overhead and the envelope, not by the format. The lever there is batching and cadence, not encoding.

---

## 4. Proposal: MeshWX v5

Design rule: **the mesh carries identifiers and numbers; the client carries tables and words.** A second rule: **nothing is flooded twice unless a human asked for it.**

### 4.1 Transport

- One data channel per deployment, e.g. `#wx-aus`. `GRP_DATA` payloads, one registered `data_type` for MeshWX (use `0xFF10` from the dev range until registered upstream in `docs/number_allocations.md`). No COBS, no name prefix, no v4 frame. Requires companion firmware ≥ v1.15 on clients; the bot's radio too.
- Never touch channel 0. Enforce at the radio layer as today.
- Requests come in as **DM text**. Responses go out as a `GRP_DATA` broadcast on the data channel if the request came from a MeshWX client, or as a single direct-routed DM text if it came from a human (§4.6).
- No discovery channel. The bot adverts as a chat node named `WX-AUS` with its lat/lon; the app derives `#wx-aus` from the name and lists nearby bots from adverts it already receives. Zero extra airtime, zero extra channel slot.

### 4.2 Common header (2 bytes)

```
byte 0  seq        uint8, per-bot, increments every message. Solves repeater dedupe (F7),
                   gives clients gap detection (replaces v4's 2-byte seq).
byte 1  msg_type   high nibble = type, low nibble = type-specific flags
```

Version is implied by `data_type`; bump the `data_type` for a breaking change rather than spending a byte per message.

### 4.3 Warning (type 0x1) — the product that matters most

```
byte 2     event      VTEC phenomenon+significance index (one byte, table in protocol.json:
                      TO.W=0x01 SV.W=0x02 FF.W=0x03 FA.W=0x04 FA.Y=0x05 FL.W=0x06 ...
                      SPS=0x40 ... TO.A/SV.A watches 0x20/0x21). ~70 entries.
byte 3     office     WFO index (125 entries, bundled wfos.json)
bytes 4-5  etn        uint16 event tracking number
bytes 6-9  expires    uint32 Unix minute (keep: absolute, NWS-authoritative)
byte 10    flags      bit7: polygon follows, bit6: zone list follows,
                      bits 4-5: tornado tag (0 none,1 possible,2 radar indicated,3 observed/emergency)
                      bits 0-3: reserved
byte 11    hail       hail tag in ¼ inch (0 = none)      } from pyIEM SBW tags —
byte 12    wind       wind tag in mph (0 = none)          } the two numbers people care about

if polygon:  n uint8, then lat0/lon0 int24 ×1e4 (6 B), then (n−1) × int8 pairs at 0.01° (2 B each).
             If any delta exceeds ±1.27°, set flags bit3 and use int16 ×0.001 pairs (4 B each).
if zones:    k uint8, then k × 4 bytes: [state|C/Z flag in bit7][start uint16][run_len uint8]
             Consecutive zone/county numbers are a single run.
```

Sizes: SVR polygon with 6 vertices = 13 + 1 + 6 + 10 = **30 bytes** (today: 125). Winter storm warning over TXZ191–TXZ208 = 13 + 1 + 4 = **18 bytes** (today: ~136). Identity for updates/cancels = (event, office, etn) which is already the VTEC key; the client displays the name from the table, the expiry countdown from its own clock, and the affected area from bundled zone/county polygons (county polygons need adding to the bundle; ~3,200 counties, similar size to zones).

**Cancel / early expiry (type 0x2):** `[seq][0x20][event][office][etn]` = **6 bytes.** Sent when VTEC action is CAN/EXP/UPG before the stored expiry.

**No headline on the wire.** If a client wants the human text (e.g. for a notification body), it renders `"{EVENT_NAME} until {local time}"` from the table. The full NWS narrative is available on request (§4.6) and never broadcast.

### 4.4 Active-warning digest (type 0x3)

Replaces the 6 h full re-broadcast. `[seq][0x30][count]` + count × `[event][office][etn][expires uint16 = minutes from now / 4]` = 3 + 6 per warning. Five active warnings = **33 bytes**, every 3 h, or immediately after any cancel. A client that sees an identity it does not hold sends one request DM for it. This turns loss recovery from "everyone receives everything again" into "one client asks for one thing".

### 4.5 Observations and forecasts

- **Observation batch (type 0x4):** `[seq][0x40][ts uint16 minutes-of-day][n]` + n × 10 bytes `[station idx uint16][temp i8][dewpt i8][wind dir/spd 1][gust 1][sky|wx 1][vis 1][pressure 1][rh 1]`. Four Austin-area METARs (KAUS, KATT, KGTU, KHYI) in one 44-byte packet every 60 min. Today that is four separate packets or, in practice, one station only.
- **Forecast (type 0x5):** keep the v3 7-byte period but drop `period_id` and `condition_flags` into a shared nibble: `[hi i8][lo i8][pop u8][sky|wx u8][wind u8]` = 5 bytes × 7 = 35 + 4 header = **~40 bytes**, every 6 h per point. PFM points stay the location key.
- **Nowcast/outlook/fire/climate:** request-only. They are nice-to-have and each broadcast is a flood through every repeater.

### 4.6 One request grammar for apps and humans

Make the *text* command the protocol. Whatever a human types in a DM is exactly what the app sends:

```
w            active warnings near my home (app) / near the bot (human)
w TXC453     warnings for a county or zone
w SV.W.EWX.42  full text of one warning (paginated, on request only)
o            latest observation batch
o KAUS       one station
f            7-day forecast for the bot's home point
f 102        forecast for PFM point index 102 (app) / f austin (human resolves via resolver)
```

The bot decides the response channel from the sender: if the sender's pubkey is a MeshWX client (it has sent an `hello v5` DM once, or simply if the request is one of the app forms like `f 102`), respond on the data channel as `GRP_DATA` so everyone benefits; otherwise reply with **one** direct DM of ≤ 120 chars, no nudge, no pagination unless the human types `more`. Never reply on a channel. This removes the WXQ/MWX hex path, the `0x02` request, the `0x03` not-available message, and the channel-echo handling. A not-available answer becomes a 1-packet DM or a 4-byte `GRP_DATA` `[seq][0xF0][type][reason]`.

Human-text replies should be compact and structured, e.g. `SVR til 4:45PM Travis,Hays. hail 1in wind 60mph` (48 chars, one 16-byte AES block fewer than a full-width reply).

### 4.7 Cadence and repeat policy

| Event | Action |
|---|---|
| New warning in coverage | Send immediately on ingest (not on a 5-min tick). Life-safety types (TO.W, SV.W, FF.W, EW.W) get **one** repeat after 90 s with a new seq. Everything else: once. |
| VTEC CON/EXT that changes expiry by ≥ 30 min or changes tags | Send once. Otherwise suppress. |
| CAN/EXP/UPG | 6-byte cancel, once. |
| Digest | Every 3 h, plus 60 s after any cancel. |
| Observations | Batched, hourly (30 min during active TO.W/SV.W in coverage). |
| Forecast | Every 6 h. |
| Adverts | Every 24 h (keep). Never advert in response to an unknown sender. |
| Radar / QPF / AFD | Request-only, or off. |

### 4.8 Coverage

Replace state-level coverage with a radius: the operator sets a center and a radius (default 120 km), and the bot precomputes the set of zone and county UGCs whose polygon intersects that circle. Polygons (SVR/TOR) are tested against the circle directly. State-wide coverage is still available but must be asked for explicitly.

### 4.9 Airtime comparison

Per message, 0 hops, US preset. Multiply by (1 + repeaters) for the real cost.

| Message | Today | v5 |
|---|---|---|
| Severe thunderstorm warning (polygon) | 582 ms | 214 ms |
| Zone-list warning (12 zones) | 582 ms | 214 ms |
| Warning update that only extends expiry | 582 ms | 0 (suppressed) or 214 ms |
| Cancel | not sent | 162 ms |
| Loss recovery | 3 × 582 ms every 6 h, to everyone | 254 ms digest every 3 h + one 200 ms request/response pair per actual loss |
| Observation, 4 stations | 4 × 254 ms | 254 ms |
| Forecast | 398 ms | 254 ms |
| On-demand answer | 2 × 582 ms | 214 ms |
| Human text query, path known | flood reply + nudge on channel | 1 direct DM ≈ 450 ms on the path only |

Whole-mesh budget for a bad spring day with Austin-only coverage (say 15 warning events, 20 updates of which 5 material, 3 cancels, 24 obs batches, 4 forecasts, 8 digests): v5 ≈ 15×214×2 (repeat) + 5×214 + 3×162 + 24×254 + 4×254 + 8×254 ≈ **17 s of originator airtime per day**, before repeaters. Today's schedule on the same day, with state-wide coverage, produced on the order of 350 delta runs × ~600 ms plus full refreshes ≈ **4–5 minutes**, before repeaters. On a quiet day v5 is ~9 s.

### 4.10 Open protocol deliverables

- `docs/MeshWX_v5_Spec.md`: the byte layouts above, the `data_type` value, the event/office/state tables as `protocol.json` (already exists, extend it), and **test vectors** (hex in → JSON out) for every message type.
- A reference decoder in pure Python with no dependencies (`meshwx/decode.py`) and a Swift port for the iOS app; both run the test vectors in CI.
- Registration request upstream for a permanent `data_type` in MeshCore's `number_allocations.md`.
- License the spec and reference decoders permissively (MIT or CC0), separate from the bot.

---

## 5. Migration

1. **Now, no protocol change:** fix coverage (F1) to Austin radius; change the delta fingerprint to `(event, office, etn, expiry rounded to 30 min, hail, wind)` (F3); drop the double send (F5); make `warnings-full` 24 h. This alone cuts most airtime.
2. **Transport:** add a `send_channel_data()` wrapper to `radio.py` that builds `[62][chan][0xFF][data_type LE16][data]` via `commands.send()`; subscribe to `EventType.CHANNEL_DATA_RECV`. Confirm firmware ≥ v1.15 on the bot radio and on the app users' radios.
3. **Encoder:** implement v5 warning/cancel/digest/obs-batch/forecast in a new `protocol/v5.py` with test vectors; delete the duplicate builders in `broadcaster.py` and have the reactive path call the executor (F12).
4. **App:** decode `GRP_DATA`, bundle county polygons, render names from tables, send requests as the text grammar.
5. **Bot request handling:** replace WXQ/MWX with the text grammar; DM-only replies; remove channel nudges and reactive adverts.
6. **Retire** `#meshwx-discover`, COBS, v3/v4 docs (mark superseded).

## 6. Things I could not verify

- The bot radio's actual advert name (log shows `Node: ?`); commit 758b17b says the prefix is ~20 bytes. Measure with a 1-character name before and after to confirm the per-packet saving.
- The exact firmware versions running on the Austin repeaters and on app users' radios. `GRP_DATA` needs ≥ v1.15 to be *received*; older firmware ignores the payload type silently.
- Whether the Austin mesh runs the SF7/62.5 preset. If it runs an SF9–SF11 preset, every airtime number above is 3–8× larger and the case for v5 is proportionally stronger.
- Repeater count in typical range of the bot. The "+3 repeaters" column in the working notes assumed three.
- The agent's reading of firmware v1.17.1 is that companion firmware *does* emit `PUSH_CODE_LOG_RX_DATA` while an app is connected (`companion MyMesh.cpp:286-297`), contradicting the project note that only repeater firmware does. This may be a firmware-version difference; worth re-testing the observer on a v1.17 companion.

---

## 7. Multi-operator deployments and MeshCore regions

Added 2026-09-10 after the review, in answer to "how do other people replicate this in their area, and can it use MeshCore regions?"

### 7.1 What MeshCore regions actually are (firmware ≥ v1.10, defaults changed in v1.15/1.16)

Regions are a **repeater-side flood filter**, not an addressing scheme.

- A sender can mark a flood as *scoped* (`ROUTE_TYPE_TRANSPORT_FLOOD`, header route bits `00`) and add 4 bytes of transport codes. `transport_codes[0] = HMAC-SHA256(region key, payload_type ‖ payload)[0:2]` (`helpers/TransportKeyStore.cpp:4-16`). For a public hashtag region the key is simply `SHA256("#name")` (`RegionMap.cpp:173-188`), so anyone who knows the name can send into it. `$private` regions use keys loaded from a keystore that is still a TODO in the source.
- Each repeater holds a `RegionMap` of up to 32 named entries plus a wildcard `*` (`helpers/RegionMap.h`). On receiving a scoped flood it recomputes the code with every region it has and forwards only on an exact match that is not `denyf` (`RegionMap.cpp:190-205`, `simple_repeater/MyMesh.cpp:434-459, 556-569`). The parent/child tree is for management only; matching is flat by name.
- Unscoped floods (what every companion sends today) are forwarded only if the wildcard allows flooding and the hop count is under `flood.max.unscoped` (default 64). A community that wants to stop "noisy neighbours" sets `region denyf *` or `flood.max.unscoped 3` on its repeaters (`docs/cli_commands.md:690-695, 773-791`).
- The companion sets its scope with `CMD_SET_DEFAULT_FLOOD_SCOPE (63)` / `CMD_SET_FLOOD_SCOPE_KEY (54)`; when set, every channel message, DM, and ACK it floods is scoped (`companion_radio/MyMesh.cpp:489-521`). Repeaters reply in the requester's scope (`simple_repeater/MyMesh.cpp:414-430`).
- There is no per-packet hop limit a sender can set. Physical range, repeater hop limits, and region scope are the only containment tools, and two of the three belong to the repeater operators.

Consequences for the weather bot:

1. **The bot must send in whatever scope the local mesh uses**, otherwise a mesh that has turned on `denyf *` drops it at hop 0. Make the scope name a config item (`MCW_FLOOD_SCOPE=#atx`, empty = unscoped) and set it on the radio at startup via command 63.
2. **Scope is the right containment for warnings.** If the Austin repeaters carry `#atx`, an Austin bot's floods stop at the edge of the repeaters that know `#atx`; a Waco bot scoped `#waco` never reaches Austin's repeaters. This is exactly the "don't spend San Antonio's airtime on Austin's floods" problem, solved by the people who own the airtime rather than by the bot.
3. **It only works if the community has adopted regions.** Whether the Austin mesh has, and what the region is called, is a question for the repeater operators. Until then the containment is physical range plus coverage radius.
4. The bot's own DMs and ACKs inherit the scope automatically, so nothing changes in the request path.

### 7.2 Recommended multi-operator architecture

**One well-known channel for the whole protocol: `#meshwx`.** Not one per city. Reasons: a channel slot is scarce (8 per device) and a traveller should not have to re-join per city; region scope and coverage radius already keep floods local; and every v5 warning is globally unique by `(event, office, etn)`, so a client that hears two bots dedupes trivially. Per-city channels solved isolation in v4 because nothing else did; with scoping and radius coverage they only cost slots. Keep the channel name versioned by the `data_type` value, not by the name, so `#meshwx` never has to change.

**Bot identity in every message.** Add one byte to the v5 header: `bot` = first byte of the bot's public key. Clients then know which bot said what, can prefer the nearest one, and can request from it by DM. Header becomes `[seq][bot][type|flags]` = 3 bytes.

**Discovery by advert.** Bots advert as chat nodes named `WX-<IATA>` (e.g. `WX-AUS`, `WX-SAT`) with lat/lon. Every MeshCore app already collects adverts, so an app lists nearby weather bots for free and DMs the closest. No discovery channel, no beacon.

**Overlap rule between neighbouring bots.** Austin and San Antonio both legitimately cover Hays County. Rather than coordinate by hand, each bot listens for other `WX-*` adverts it can hear (which is precisely the set of bots whose floods overlap its own) and applies a deterministic ownership rule: *broadcast a warning only if you are the nearest known WX bot to the warning's centroid* (polygon centroid, or centroid of the covered zones). Ties break on lower pubkey. Bots that cannot hear each other cannot overlap much, and both send, which is harmless. Requests are always answered by the bot that was asked.

**Same feed, no server-side federation.** Every bot runs the same container against the same national EMWIN feed and parses independently. Nothing needs to be shared between operators. CoreScope/MQTT can observe other bots' output for monitoring but is not part of the protocol.

**Operator config surface for a replica** (everything else is derived):

```
MCW_CALLSIGN=WX-SAT           # advert name
MCW_CENTER=29.42,-98.49       # coverage centre
MCW_RADIUS_KM=120
MCW_FLOOD_SCOPE=#satx         # or empty if the local mesh is unscoped
MCW_SERIAL_PORT=...
```

Channel (`#meshwx`), data_type, tables, and schedule defaults are fixed by the protocol; the bot derives zones, counties, PFM points, and METAR stations from centre and radius on first start.

### 7.3 What this needs from the community, not from the code

- Agreement on the shared channel name and the registered `data_type` (submit to MeshCore's `docs/number_allocations.md`).
- The Austin repeater operators' region name, if they use one, and confirmation that they allow flood for it.
- Firmware on the repeaters matters too, and I could not verify it: `Mesh::onRecvPacket()` refuses to flood-route payload types it does not know (`Mesh.cpp:326-329`). Repeaters built before GRP_DATA (0x06) entered the enum would drop v5 packets as unknown. The shallow clone did not show when 0x06 was added; check a pre-v1.15 `Packet.h` before relying on old repeaters, or ask the Austin repeater operators for their versions. Client radios need ≥ v1.15 to receive GRP_DATA.

---

## 8. Revisions to the v5 proposal (2026-09-13, after the satellite inspection)

These supersede the corresponding parts of section 4 and 7.

1. **One channel per deployment, carrying both binary and text.** Drop the shared `#meshwx` and the separate human text channel. `#wx-aus` carries GRP_DATA for apps and, for life-safety events only, one GRP_TXT line for everyone else. Group text and group data share a channel key, so a plain MeshCore app sees only the text and a MeshWX app sees both. One slot to join, local by construction, and the app derives the channel from the bot's advert name (`WX-AUS` → `#wx-aus`). Travelers re-join automatically when the app hears a new bot.

2. **Flood tags in the warning message.** pyIEM exposes them and they are the actionable part of a flash flood warning. Tag bytes become: `hail` (¼ in, u8), `wind` (mph, u8), and one flags byte with `tornado` (2 bits: none / possible / radar indicated / observed), `flood_source` (2 bits: none / radar / radar+gauge / observed), `flood_damage` (2 bits: none / considerable / catastrophic), plus polygon-present and zones-present bits.

3. **Counties are first-class.** Every area entry is `[state (7 bits) | C/Z (1 bit)][start u16][run u8]`. Severe, tornado, and flash flood warnings are county products; 4 of the 11 active warnings observed had no zones at all. Client bundles ship county polygons alongside zones.

4. **One polygon encoding, not two.** Always int16 deltas at 0.001° (4 bytes per vertex after the 6-byte first vertex). The int8 variant with an escape flag saved ~10 bytes on a typical polygon, which usually does not change the AES block count and is not worth a second code path in every implementation.

5. **Two-byte bot identity.** One byte of pubkey prefix collides 1 in 256; two bytes is cheap. Header becomes `[seq u8][bot u16][type|flags u8]` = 4 bytes.

6. **Absolute time in the digest.** GRP_DATA carries no timestamp, and a phone can drain its radio's offline queue hours later, so "expires in N minutes from now" is ambiguous. The digest header gets `now` as a uint32 Unix minute; entries stay relative to it (2 bytes each). Warnings keep the absolute uint32 expiry.

7. **Feed-health byte in the digest.** Minutes since the bot last received any product from its home WFO, capped at 255 in 4-minute units. The satellite hop loses 1–4 % of products and had a 10-hour outage on day one; clients should be able to show "feed stale" instead of implying silence means calm.

8. **Explicit request routing instead of guessing the client.** Human commands are bare (`w`, `f austin`) and answered by direct DM text. App commands are the same grammar prefixed with `>` (`>f 102`) and answered as GRP_DATA on the deployment channel. No registration handshake, no heuristics.

Two smaller rules for the spec: design every common message to land just under a 16-byte AES boundary (13, 29, 45, 61 bytes of data), since padding is free up to the boundary and a full block past it; and define that receivers ignore unknown message types, with types 0xC–0xF reserved for third-party experiments.

One known limitation to state plainly rather than fix in v5: anyone with the channel key can inject a fake warning, exactly as with any MeshCore hashtag channel. A signature would cost 64 bytes per message. The bot's advert is signed, so a client can at least verify that a bot with that pubkey exists nearby; message-level authenticity is a v6 problem.

---

## 9. Location resolution accuracy (2026-09-13)

Tested the resolver (`geodata/__init__.py`) against ground truth: true nearest station by haversine over `stations.json`, true zone by point-in-polygon over the bundled `zones.geojson`, stations that actually reported today over the satellite, and the forecast points present in today's `PFMEWXTX`.

| Check | Result |
|---|---|
| Nearest METAR station, 44 Central Texas queries | Correct in 44/44 (geometry is right; "Round Rock, TX" → KGTU at 17 km, KEDC a near tie at 17.4 km) |
| Chosen station reported today | 30/30, but only by luck: nothing in the code checks. If the nearest station is silent the observation falls to an RWR city row, not to the next station |
| Zone assignment, 30 Central Texas towns | 29/30 (Cedar Park got Travis TXZ192; it is in Williamson TXZ173) |
| Zone assignment, 400 random Texas places | **wrong for 14.2 %** |
| Zone assignment, 600 random US places | **wrong for 27.2 %** (the zone table mixes public, fire, and coastal zones; nearest centroid routinely picks the wrong family) |
| Bare name without state | Picks the first alphabetical match: `round rock` → Round Rock AZ, `georgetown` → AR, `jackson` → AL |
| Duplicate name within a state | 207 (name, state) pairs in `places.json`, 21 in Texas. `Lago Vista TX` resolves to a Starr County colonia on the Rio Grande (26.56, −99.11) instead of the Travis County city |
| Forecast point for a zone | 11 of the 20 zones within 120 km of Austin have no PFM point of their own (Hays, Comal, Bastrop, Blanco, Llano, Lee, Kendall, Gonzales, Lampasas, Milam, Burleson). `encode_forecast_from_pfm` requires an exact zone match and returns None, so Kyle, Buda, San Marcos, Wimberley, Dripping Springs, Bastrop, and New Braunfels all fall back to the ZFP narrative regex |

### Required fixes (all resolver-side, no protocol impact)

1. **Zone by polygon, not centroid.** Point-in-polygon over `zones.geojson` (already bundled; shapely is already a dependency via pyIEM). Restrict to public forecast zones for forecasts and warnings-near; fire and marine zones are separate lookups. Precompute once per place at startup or use an STRtree.
2. **Nearest station that reports.** Choose the nearest station with a METAR in the store newer than 2 hours; fall back outward. Report the station's distance in the text reply so the user can judge it (`KGTU 17km`).
3. **Nearest forecast point by distance, not zone equality.** Use the PFM point closest to the place across all PFM products in the store, with the distance shown. San Marcos gets San Marcos Airport (7 km), Kyle gets it too (12 km), Bastrop gets La Grange or Camp Mabry, and none of them get a narrative regex.
4. **Disambiguate by proximity to the bot.** For bare names and for duplicate (name, state) pairs, prefer the candidate nearest the bot's coverage centre, then the one with the largest population. Add population to `places.json` at bundle build time (the Census source has it) so a national query still picks Springfield MO over Springfield AR when neither is nearby.
5. **Never answer with silent fallback.** If the resolver's confidence is low (ambiguous name, station > 50 km, zone from a fallback), say so in the reply: `Round Rock TX (KGTU 17km)`. Accuracy the user cannot see is not accuracy.

### What this does not change

The wire format. All of this is server-side resolution; the client still sends a place, station, or zone reference and receives the same messages.
