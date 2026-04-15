# Future Project: Standalone E-Ink Weather Dashboard

> **Status: idea, parked for later.** This document captures the concept and feasibility analysis so it can be picked up again in a future session without losing context. Nothing here is implemented yet.

## The concept

A small, low-power, standalone hardware device that:

- Houses an MCU (ESP32 or nRF52) + LoRa radio + e-ink display + battery
- Joins the bot's MeshWX broadcast channel as a passive receiver
- Decodes the bot's binary weather broadcasts (`0x10` radar, `0x20`/`0x21` warnings, `0x30` observation, `0x31` forecast)
- Renders a weather dashboard to the e-ink display
- Updates automatically on each broadcast cycle (typically hourly)
- Runs for months on a single charge, indefinitely with a small solar panel
- Costs ~$30-50 in parts at hobbyist scale, sub-$20 BOM at production scale

The user-facing pitch: "**A real weather station that doesn't need WiFi or the internet — it pulls from a local mesh radio bot and shows you exactly what's happening at your location, refreshed every hour.**"

## Why this is a great fit for the existing protocol

Every architectural decision in MeshWX (v3/v4) happens to be exactly what an embedded receiver wants. None of it was deliberately chosen for the e-ink use case, but it falls out of the airtime-first principle:

- **Structured binary fields, not text** — an MCU can decode a `0x30` observation in ~50 lines of C: read 16 bytes, unpack int8s, look up sky code in a static table, render to display. No string parsing, no JSON, no allocations.
- **Periodic broadcasts at human time scales** — bot pushes a full cycle once per `MCW_MESHWX_BROADCAST_INTERVAL` (default 3600s = 1 hour). E-ink refreshes naturally fit that cadence (1-3 seconds per refresh, ~50,000 refresh lifetime → years of hourly updates).
- **COBS encoding eliminates null bytes** — same trick that fixes the meshcore firmware companion-protocol truncation also makes parsing trivially robust on a tiny MCU buffer.
- **136-byte max payload** — fits in any LoRa-class radio buffer with room to spare.
- **Tiny per-message size** — observation 16B, forecast 21B, warning 50-100B, radar grid 134B. Whole hourly cycle is well under 1 KB.
- **Home location proactive broadcasts (commit `50b660f`)** — the bot pushes obs + forecast for the home city automatically. The device doesn't need to send anything; it just listens.
- **Absolute expiry timestamps (commit `202007e`)** — receiver computes "expired" from its own clock with no server-time math, no wraparound bugs.
- **v4 sequence numbers** — receiver can detect gaps and measure link quality without any back-chatter.
- **v4 FEC/XOR parity** — if the receiver misses one radar quadrant, it can recover it from the parity unit. Perfect for lossy multi-hop paths.
- **v4 discovery beacons** — a new device can find nearby bots by listening on `#meshwx-discover` instead of being pre-configured with a channel name.

The bot we've built is, accidentally on purpose, **already the perfect data source for this device**.

## Hardware candidates

### Best off-the-shelf option: LilyGO T-Echo
- **MCU**: nRF52840
- **Radio**: SX1262 LoRa (SX1268 in some variants)
- **Display**: 1.54" monochrome e-paper, 200×200 px
- **Extras**: GPS, two buttons, accelerometer, 800 mAh LiPo, USB-C charging
- **Cost**: ~$50 retail
- **Status**: Already runs Meshcore firmware as one of the supported targets in the Meshcore community
- **Form factor**: roughly the size of a deck of cards, ~30g

This is the closest off-the-shelf product to "the device described above" today. Building the dashboard means writing application firmware that runs alongside (or instead of) the Meshcore messenger code, decodes our binary broadcast messages, and drives the existing e-paper.

### Other viable boards
- **Heltec Mesh Node T114** — nRF52840 + SX1262 + 1.14" color TFT (not e-paper but lower BOM)
- **RAK4631 Wireless Module + Waveshare e-paper** — modular nRF52840 + SX1262, your choice of 2.9"/4.2"/7.5" e-ink, more BOM work but bigger displays available
- **Heltec WiFi LoRa 32 V3 + Waveshare 4.2" e-paper** — ESP32-S3 + SX1262 instead of nRF; higher current draw but easier development environment
- **Custom PCB** — nRF52840 module + SX1262 module + Waveshare 4.2" e-paper + LiPo + boost converter → sub-$20 BOM at production scale, fully designed for the use case

For a finished product, a custom PCB built around the nRF52840 + SX1262 combo with a 4.2" display is the right answer. For prototyping, the T-Echo is the fastest path.

## Software architecture sketch

Three layers, totaling roughly 1500-2500 lines of C/C++:

```
┌────────────────────────────────────────────────────┐
│ Application                                         │
│   - State machine: idle → receive → render → sleep │
│   - Cache: latest obs, latest forecast, active warn │
│   - Layout engine: places fields on the e-paper     │
│   - Refresh policy: full vs partial, when to wake   │
├────────────────────────────────────────────────────┤
│ MeshWX decoder (port from meshcore_weather/protocol)│
│   - cobs_decode (~30 lines)                         │
│   - unpack_observation (~80 lines)                  │
│   - unpack_forecast (~60 lines, 7 periods × 7 bytes)│
│   - unpack_warning_polygon / _zones (~150 lines)    │
│   - unpack_radar_grid (~40 lines)                   │
├────────────────────────────────────────────────────┤
│ Meshcore stack                                      │
│   - Already in the existing Meshcore firmware       │
│   - Hooks: on_channel_msg(channel_id, payload, len) │
└────────────────────────────────────────────────────┘
```

**The MeshWX decoder layer is a straightforward C port of `meshcore_weather/protocol/meshwx.py`.** Every pack/unpack function in our Python code maps cleanly to a C function operating on a `uint8_t*` buffer. Estimated effort: a few hours of focused work to produce a single-header library.

## Embedded preload bundle (NOT the full 9.9 MB)

The full `client_data/` bundle is way too big for a microcontroller, but **the device only needs data for its own home location**, which is dramatically smaller. At provisioning time (USB serial config or BLE pairing), bake in:

| Item | Size | Purpose |
|---|---|---|
| Home zone code | 6 bytes | "TXZ192" — to recognize warnings + forecasts targeting it |
| Home METAR ICAO | 4 bytes | "KATT" — to recognize observations |
| Home PFM point ID | 4 bytes | uint32 — to recognize PFM-keyed forecasts |
| Home name string | ~30 bytes | "Austin TX" — for display |
| Home lat/lon | 8 bytes | for distance-to-warning calculations |
| State index table | ~580 bytes | to decode `state_idx` in zone refs |
| Sky-code → glyph table | ~100 bytes | static lookup for icon rendering |
| Compass nibble → label | ~64 bytes | 16 wind direction strings |
| Warning type → label table | ~100 bytes | "Tornado", "Severe TStorm", etc. |
| **Total** | **~1 KB** | fits in any nRF52 / ESP32 flash |

For drawing warning shapes on a tiny e-ink (probably not needed at low res), a regional zone polygon subset of ~50-200 KB still fits.

## Display layout sketches

### 1.54" / 200×200 (T-Echo)

```
┌──────────────────────────────┐
│ AUSTIN, TX           Sat 11AM│
├──────────────────────────────┤
│  ☼   72°F                    │
│      ↑78  ↓64                │
│      Partly Cloudy           │
│      SE 9 mph                │
├──────────────────────────────┤
│ Sat Sun Mon Tue Wed Thu Fri  │
│ ⛅82 ⛈78 ⛈70 ☼75 ☼80 ⛅82 ☼84│
│ /64 /67 /58 /55 /60 /65 /68  │
└──────────────────────────────┘
```

### 4.2" / 400×300 (Waveshare module)

Add: dewpoint/RH bar, wind compass rose, pressure trend arrow, mini radar grid (16×16 from `0x10` messages — perfect resolution for that size), active-warning banner with countdown timer, larger forecast strip with full condition icons.

### 7.5" / 800×480 (Waveshare module)

Add: small condition icon for each forecast day, tiny line graph of temperature trend across the 7 days, multi-zone warning list, mini map showing the bot's coverage area with warning polygons rendered.

## Power budget (nRF52840 + SX1262 + e-paper, hourly updates)

| State | Current | Time/hour | Energy/hour |
|---|---|---|---|
| Deep sleep | 5 µA | ~58 minutes | 0.005 mAh |
| Radio receive (window around expected broadcast) | 10 mA | ~2 minutes | 0.33 mAh |
| MCU active processing | 5 mA | ~5 seconds | 0.007 mAh |
| E-paper refresh | 20 mA | ~3 seconds | 0.017 mAh |
| **Total per hour** | | | **~0.36 mAh** |
| **Per day** | | | **~8.6 mAh** |

A 1000 mAh LiPo (matchbox-sized) gives **~110 days of operation** between charges. Add a 2-watt solar panel and it runs forever. The dominant cost is the receive window — if we tighten that with broadcast time prediction (the bot broadcasts at predictable times so the receiver can wake just before each expected broadcast), battery life can stretch to **6+ months on a single charge**.

This is fundamentally a low-power use case because of the 1-hour update cadence. A device targeting 5-minute updates would have a very different power profile (probably needs always-on receive).

## What's missing on the bot side

Honestly, almost nothing. Run through the checklist:

- ✅ Periodic obs broadcast for home city — done in `50b660f`
- ✅ Periodic forecast broadcast for home city — done in `50b660f`
- ✅ Periodic warnings broadcast for coverage area — done in Phase 1
- ✅ Periodic radar broadcast for coverage area — done in Phase 1
- ✅ Compact binary format with COBS encoding — done in v3
- ✅ Absolute expiry timestamps so receivers can compute "expired" without server math — done in v3
- ✅ Proactive nature: receiver doesn't need to send anything — done
- ✅ FEC/XOR parity for radar quadrants — done in v4
- ✅ Sequence numbers for gap detection and link quality — done in v4
- ✅ Discovery beacons on `#meshwx-discover` — done in v4
- ✅ Fire weather forecasts (FWF) — done in v4
- ✅ Nowcast (NOW) short-term forecasts — done in v4

**One nice-to-have for embedded receivers: a periodic heartbeat broadcast.** The v4 discovery beacon (`0xF0`) on `#meshwx-discover` is close to this — a sleeping device could listen on the discover channel for a beacon to know when the next broadcast cycle is coming. A dedicated heartbeat on the data channel with a "next cycle in N seconds" field would be even better for power optimization.

## Implementation phases (when we come back to this)

1. **Phase 1 — MVP (1-2 weekends)**: Buy a T-Echo. Install Meshcore firmware. Write a sketch on a separate dev board that demonstrates receiving + decoding a `0x30` observation broadcast and printing it to serial. Goal: prove the protocol works on hardware.

2. **Phase 2 — Single-screen display (1 week)**: Add e-paper rendering on the T-Echo. Show the latest observation as plain text. Update on each broadcast. No layout polish yet.

3. **Phase 3 — Full dashboard (1-2 weeks)**: Add the forecast strip, warning banner, sky icons, and a refresh policy that handles full + partial e-paper refreshes intelligently. Persist last-known state across reboots in flash.

4. **Phase 4 — Power optimization (a few days)**: Add deep sleep, broadcast time prediction, low-battery handling, optional solar charging support.

5. **Phase 5 — Productization (open-ended)**: Custom enclosure, OTA firmware updates over BLE, configuration UI (probably via BLE companion app), multi-location support, possibly a small button UI for switching between locations or viewing the radar grid.

MVP through Phase 3 is realistically **2-4 weeks of evening/weekend hacking** for someone comfortable with embedded C and the existing Meshcore stack.

## Things to do on the bot side first (when we come back to this)

These are what I'd want to add to MeshWX before targeting an embedded receiver as a first-class consumer:

1. **Heartbeat broadcast on data channel** — the v4 discovery beacon exists but only on `#meshwx-discover`. A data-channel heartbeat with "next cycle in N seconds" would let devices sleep more precisely.
2. **Single-header C decoder library** — port the relevant pieces of `meshcore_weather/protocol/meshwx.py` to a clean self-contained C/C++ header file (`meshwx.h`) that any Arduino/PlatformIO project can drop in. Keep it allocation-free, no malloc, fixed-size buffers. Must handle v4 frame unwrapping + FEC recovery.
3. **Document the embedded receiver use case** in `docs/` — once it has a real reference implementation, it should be a first-class consumer of the protocol alongside the iOS app, not an afterthought.

## Open questions / decisions for the future-us session

When we come back to this, here are the things to decide before writing any code:

1. **Hardware target**: T-Echo (off-the-shelf, fastest start), Heltec/RAK + Waveshare (more flexible, bigger displays), or custom PCB (for a real product)?
2. **Display size**: 1.54", 2.9", 4.2", 7.5"? Smaller = lower power + cheaper, bigger = richer dashboard.
3. **Application firmware approach**: Write fresh application firmware that calls into Meshcore as a library? Fork the Meshcore messenger and add a "weather mode"? Run alongside Meshcore as a separate "companion" app?
4. **Provisioning UX**: How does the user set their home location? Buttons + display menu? BLE companion app on phone? USB serial console? Pre-configured at sale time?
5. **Multi-location support**: Single home or N homes? If N, does each location consume the same bot's broadcasts (has to be in the bot's coverage area) or do we need a "request specific location" path that wakes the receiver to send a `0x02` DM?
6. **Display refresh policy**: Always refresh on every received broadcast? Refresh only on data changes (saves e-paper lifetime)? Different policies for warnings (immediate) vs forecast (gentle)?
7. **Branding / market**: Hobby kit (sold as PCB + parts), assembled product, open-source reference design, commercial product?

## Why this matters

A hardware product fundamentally changes the value proposition of the meshcore-weather bot from "data source for an iOS app" to "infrastructure for a self-contained off-grid weather information network." The bot already does the hard part — pulling NWS EMWIN data, parsing it canonically with pyIEM, broadcasting in a tight binary format. The dashboard is just the missing user-facing endpoint that doesn't require a smartphone, an internet connection, or a paid app.

For users who want weather information at a remote cabin, on a boat, in a rural area without cell coverage, or in a community that's set up a private mesh, this is the device that closes the loop. The bot is the infrastructure; the dashboard is what people put on their wall.

## References for when we come back

- **Meshcore firmware repo**: github.com/ripplebiz/MeshCore (check current state — names/orgs may have changed)
- **LilyGO T-Echo product page**: lilygo.cc — search for "T-Echo nRF52840"
- **Waveshare e-paper modules**: waveshare.com/product/displays/e-paper.htm
- **GxEPD2 library**: github.com/ZinggJM/GxEPD2 (Arduino e-paper driver, supports most Waveshare displays)
- **Existing protocol reference**: `meshcore_weather/protocol/meshwx.py` in this repo (the source of truth that the C decoder library would mirror)
- **Wire format spec**: `docs/MeshWX_Protocol_v4_Design.md` and `docs/v4_client_guide.md` in this repo (v3 doc is legacy reference)
- **Bot side already-shipped commits relevant to this**:
  - `50b660f` — home city obs + forecast broadcast (the data source the device consumes)
  - `9d08fde` — PFM forecast quality upgrade
  - `1091d57` — `LOC_PFM_POINT` city-search support
  - `202007e` — v3 wire format with absolute expiry timestamps (the foundation that makes embedded clients work without time sync)
- **v4 protocol additions relevant to this**: FEC/XOR parity (radar recovery), sequence numbers (link quality), discovery beacons (auto-provisioning), fire weather + nowcast (new products)

---

*Document parked for later. When we revisit this, the realistic next concrete step is "buy a T-Echo and write the C port of `meshwx.py`". Everything else is downstream of those two things.*
