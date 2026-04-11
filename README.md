# Meshcore Weather

**Off-grid weather data infrastructure for [Meshcore](https://meshcore.co) LoRa mesh networks.** Fetches NWS EMWIN weather products (forecasts, warnings, radar, observations, storm reports, etc.), parses them with canonical NWS tooling, and broadcasts them on a LoRa mesh channel as compact structured binary messages that any subscribed client — phone apps, web clients, standalone hardware displays — can decode offline, without the internet.

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  GOES-16     │     │                 │     │  #wx-broadcast   │     ┌─────────────┐
│  (future SDR)├────►│  meshcore-      ├────►│  LoRa channel    ├────►│  iOS app    │
└──────────────┘     │  weather        │     │                  │     └─────────────┘
                     │                 │     │  0x10 Radar      │     ┌─────────────┐
┌──────────────┐     │  • fetch        │     │  0x20 Warning    ├────►│  Web client │
│  NOAA EMWIN  │     │  • parse (pyIEM)│     │  0x30 Obs        │     └─────────────┘
│  internet    ├────►│  • schedule     │     │  0x31 Forecast   │     ┌─────────────┐
└──────────────┘     │  • broadcast    │     │  0x32 Outlook    ├────►│  E-ink      │
                     └────────┬────────┘     │  0x33 LSR        │     │  dashboard  │
                              │              │  0x34 Rain       │     │  (future)   │
                              │              │  0x36 TAF        │     └─────────────┘
                              ▼              │  0x37 Near       │
                    ┌─────────────────┐      └──────────────────┘
                    │ Web admin portal│
                    │ localhost:8080  │
                    └─────────────────┘
```

## What this gives you

- **A working operator node** that can run on a Raspberry Pi or any Linux/macOS box with a LoRa serial radio attached. Docker-compose one-liner.
- **A structured binary protocol** (MeshWX v3) designed for one-way broadcast to passive receivers. Every message is ≤136 bytes and carries enough data to drive a modern weather-app UX without any internet connectivity on the client side.
- **A per-job broadcast schedule system** with a web admin UI. Operators define arbitrary `(product, location, interval)` jobs via the portal — e.g. "radar every 15 min", "Austin METAR every 30 min", "TX storm reports every 10 min", "EWX outlook every 12 hr". Jobs persist across restarts.
- **Preload bundle** (`client_data/`, ~9.9 MB) that ships with every client app — NWS zones, census places, METAR stations, WFO metadata, PFM forecast points, zone polygons. With this preloaded, broadcasts only carry compact IDs instead of full names, slashing airtime.
- **pyIEM-powered parsing** — the reference Python library for NWS text products (VTEC, UGC, CAP standards). Runs fully offline with a `legacy_dict` UGC provider built from bundled zones data.
- **Canonical NWS data quality**: forecasts from PFM (Point Forecast Matrix) tables, warnings with correct VTEC extraction and polygon winding, absolute expiry timestamps so clients always know exactly when data becomes invalid.
- **Optional DM-based request path** for clients that want to ask for specific data on demand (e.g. iOS city search). Responses go out on the broadcast channel so every client benefits from one request.
- **Legacy text-command interface** that lets a human user on the mesh DM the bot in plain English (`wx austin`, `forecast dallas tx`, `warn OK`) and get text replies. Secondary to the binary protocol but still works.

## Status

| Area | State |
|---|---|
| EMWIN data ingestion (internet) | ✅ production |
| EMWIN data ingestion (GOES SDR) | ⏳ stubbed, pending SDR hookup |
| pyIEM canonical product parsing | ✅ shipped |
| MeshWX v3 binary wire format (radar, warning, obs, forecast, outlook, LSR, rain, METAR, TAF, warnings-near) | ✅ shipped |
| Broadcast schedule system + web portal UI | ✅ shipped (commit `7c35b33`) |
| iOS app (consumer) | 🚧 in development by third party |
| Standalone e-ink dashboard (consumer) | 💡 idea parked in `docs/Future_EInk_Dashboard.md` |
| Text-command interface | ✅ shipped (legacy) |

## Wire format at a glance

Everything is on channel 2 (`#wx-broadcast` or whatever `MCW_MESHWX_CHANNEL` says). All messages are COBS-encoded so the firmware's companion protocol doesn't truncate at null bytes. Multi-byte integers are big-endian unless noted.

| Byte 0 | Name | Contents |
|---|---|---|
| `0x01` | Refresh Request | client → bot DM (legacy v1 path) |
| `0x02` | Data Request | client → bot DM, product + location |
| `0x10` | Radar Grid | 16×16 4-bit reflectivity, one per region |
| `0x20` | Warning Polygon | storm-specific polygon + headline |
| `0x21` | Warning Zones | multi-zone advisory, compact zone-coded |
| `0x30` | Observation | current conditions (temp, dewpoint, wind, sky, vis, pressure) |
| `0x31` | Forecast | 7 daily periods with high/low/sky/PoP/wind/flags |
| `0x32` | Outlook | HWO hazards day-1 and days-2-7 |
| `0x33` | Storm Reports | up to 16 confirmed LSRs |
| `0x34` | Rain Obs | cities currently reporting precipitation |
| `0x35` | METAR | station observation (same format as 0x30) |
| `0x36` | TAF | terminal aerodrome forecast snapshot |
| `0x37` | Warnings Near Location | list of active warnings affecting a location |
| `0x40` | Text Chunk | reserved for dictionary-compressed text fallback |

Full byte-level specs: **`docs/MeshWX_Protocol_v3.md`**.

## For client developers (iOS, web, embedded)

Start here: **`docs/iOS_Developer_Brief.md`**. It covers:

- Exact wire format of every message type you might receive
- How to decode COBS, big-endian fields, `uint32` Unix-minute expiry timestamps, 16-point compass wind nibbles
- How to send a `0x02` data request as a DM (the `WXQ` text prefix convention)
- The preload bundle layout (`client_data/`) and what each file gives you
- The request → broadcast flow diagram (you send a DM, the response comes out on `#wx-broadcast` — not as a DM reply)
- The city search → forecast flow for iOS-style apps
- A debug checklist for the top six integration pitfalls

Secondary reference: **`docs/MeshWX_Protocol_v3.md`** is the canonical byte-level wire format spec. The iOS brief is the practical integration summary.

A standalone hardware product concept (e.g. nRF52 + LoRa + e-paper) is parked in **`docs/Future_EInk_Dashboard.md`** for later exploration.

## For operators

### Configure your coverage once via `.env`

```bash
MCW_HOME_CITIES=Austin TX,San Antonio TX        # Cities to broadcast obs+forecast for
MCW_HOME_STATES=TX                              # States for warning filtering
MCW_HOME_WFOS=EWX,FWD,HGX,SJT                   # NWS offices — narrows radar + warnings
```

Coverage determines which radar regions are broadcast, which warnings get filtered to your area, and which home cities get proactive obs/forecast broadcasts. On first run, the bot synthesizes a default broadcast schedule from your coverage config.

### Then manage everything else from the web admin

Open **`http://localhost:8080/schedule`** and you'll see a live table of all broadcast jobs with their last-run / next-run / bytes-sent stats. From there:

- **Add a new job** — pick a product (radar, observation, forecast, outlook, storm_reports, rain_obs, metar, taf, warnings, warnings_near), pick a location type (station, zone, wfo, pfm_point, region, coverage, city), enter the location ID, set the interval, save.
- **Enable/disable** jobs without deleting them
- **Edit** intervals, names, or targets
- **Run now** to force-broadcast a job immediately regardless of schedule
- **Delete** jobs

Changes take effect within 30 seconds (the scheduler picks up config changes on its next tick) — no restart required. The config persists at `data/broadcast_config.json` across deploys because `data/` is a Docker volume.

### Other portal pages

- `/` — dashboard with bot state and coverage summary
- `/data` — live map of active warnings and latest radar
- `/products` — EMWIN product browser (lets you inspect raw NWS text feeding the parsers)
- `/status` — radio connection state, contact list, manual broadcast trigger
- `/config` — read-only view of coverage (edit via `.env` + restart)

### Example schedule you might configure

```
ID                Name                       Product        Location       Interval
─────────────────────────────────────────────────────────────────────────────────
radar-coverage    Radar (3 TX regions)       radar          coverage       15 min
warnings-coverage Active TX warnings         warnings       coverage       5 min
obs-austin-tx     Austin current wx          observation    city:Austin TX 30 min
obs-san-antonio   San Antonio current wx     observation    city:San...    30 min
forecast-austin   Austin 7-day forecast      forecast       city:Austin TX 2 hr
forecast-sa       San Antonio 7-day          forecast       city:San...    2 hr
ewx-hwo           EWX hazardous outlook      outlook        city:Austin TX 12 hr
tx-storm-reports  TX storm reports           storm_reports  city:Austin TX 10 min
kaus-taf          KAUS TAF snapshot          taf            station:KAUS   60 min
kdfw-taf          KDFW TAF snapshot          taf            station:KDFW   60 min
```

## Quick start

### With Docker (recommended)

```bash
git clone https://github.com/pesqair/meshcore-weather.git
cd meshcore-weather
cp .env.example .env
# Edit .env with your MCW_SERIAL_PORT, MCW_MESHCORE_CHANNEL, MCW_HOME_*, etc.

docker compose up -d
```

The container will:

1. Connect to your configured serial radio (or TCP radio proxy)
2. Start fetching EMWIN data from NOAA every 2 minutes
3. Bootstrap a default broadcast schedule from your `.env` coverage config
4. Launch the web admin portal on `http://localhost:8080`
5. Start the broadcast scheduler

### Without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[radar,portal]"
cp .env.example .env
# Edit .env
meshcore-weather
```

### First-run verification

Once the bot is running, you should see log lines like:

```
INFO schedule.store: Bootstrap schedule: 4 default jobs (1 home cities → obs+forecast pairs)
INFO scheduler: Broadcast scheduler started: 4 jobs, tick every 30s
INFO portal.server: Portal running at http://0.0.0.0:8080
INFO radio: Listening on channel 3 (#digitaino-wx-bot)
INFO radio: Data channel 4 (#wx-broadcast)
```

Visit `http://localhost:8080/schedule` in a browser and you should see your 4 default jobs ticking over with live stats.

## CLI tools

The bot ships with a `meshcore-weather-cli` helper for operations and debugging:

```bash
meshcore-weather-cli fetch              # Fetch EMWIN products from NOAA into local cache
meshcore-weather-cli query "Austin TX"  # Run the text-command parser against stored data (no radio)
meshcore-weather-cli interactive        # Simulate mesh commands in a local REPL
meshcore-weather-cli contacts           # List known contacts on the radio device
meshcore-weather-cli remove <name>      # Remove a contact by name
meshcore-weather-cli clear-contacts     # Remove all contacts (fresh start)
```

## Configuration reference

All settings are environment variables prefixed with `MCW_`. See `.env.example` for the full list. Most commonly adjusted:

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_SERIAL_PORT` | `/dev/cu.usbserial-0001` | Serial port or `tcp://host:port` for a networked radio |
| `MCW_SERIAL_BAUD` | `115200` | Serial baud rate |
| `MCW_MESHCORE_CHANNEL` | `#digitaino-wx-bot` | Channel name or index for text commands (never `0`/public) |
| `MCW_MESHWX_CHANNEL` | `#wx-broadcast` | Channel name for binary data broadcasts |
| `MCW_HOME_CITIES` | *(empty)* | Comma-separated cities to seed the default schedule |
| `MCW_HOME_STATES` | *(empty)* | Comma-separated states for warning filtering |
| `MCW_HOME_WFOS` | *(empty)* | Comma-separated WFOs for coverage filtering |
| `MCW_EMWIN_SOURCE` | `internet` | `internet` or `sdr` (future) |
| `MCW_EMWIN_POLL_INTERVAL` | `120` | EMWIN refresh interval in seconds |
| `MCW_EMWIN_MAX_AGE_HOURS` | `12` | Expire products older than this |
| `MCW_PORTAL_ENABLED` | `false` | Set to `true` to enable the web admin portal |
| `MCW_PORTAL_HOST` | `127.0.0.1` | Portal bind address |
| `MCW_PORTAL_PORT` | `8080` | Portal port |
| `MCW_ADMIN_KEY` | *(empty)* | Pubkey prefix of the admin user for DM admin commands |
| `MCW_LOG_LEVEL` | `INFO` | Log level |

Once the bot is running, **the broadcast schedule is managed via `data/broadcast_config.json` and the portal**, NOT via environment variables. Env vars are bootstrap config only.

## Data sources

Every message broadcast on `#wx-broadcast` is derived from an official NWS product ingested via EMWIN. Parsing is done by [pyIEM](https://github.com/akrherz/pyIEM) (Iowa Environmental Mesonet — the reference Python library for NWS text products) wherever possible, with a few custom parsers where pyIEM doesn't cover a specific product (notably our own PFM column-position parser in `parser/pfm.py`).

Supported product types:

| Product | Source | Produces |
|---|---|---|
| **PFM** | Point Forecast Matrix | `0x31` Forecast (structured numeric data, daily aggregates) |
| **ZFP** | Zone Forecast Product | `0x31` Forecast fallback (narrative regex extraction) |
| **RWR** | Regional Weather Roundup | `0x30` Observation, `0x34` Rain Obs |
| **METAR** | SAH/aviation | `0x30` Observation, `0x35` METAR |
| **TAF** | Terminal Aerodrome Forecast | `0x36` TAF snapshot |
| **HWO** | Hazardous Weather Outlook | `0x32` Outlook (day-1 and days-2-7 hazards) |
| **LSR** | Local Storm Reports | `0x33` Storm Reports |
| **SVR/SVS/TOR/FFW/FLW/FLS/WSW/NPW/RFW/MWW/SPS/...** | NWS warnings | `0x20`/`0x21` Warning broadcasts with VTEC metadata |
| **NEXRAD composite** | IEM hosted PNG | `0x10` Radar Grid (16×16 4-bit per region) |

Warnings include canonical VTEC event tracking (phenomenon / significance / action / ETN / office), correct polygon winding, both zone (`TXZ192`) and county FIPS (`TXC029`) UGC support, and absolute expiry timestamps so clients never display stale or "fake-extended" warnings.

## Text-command interface (legacy)

The bot also supports a human-friendly text command interface via channel messages or DMs. This is the **original** interface and predates the binary protocol. It still works and is useful for debugging the data pipeline from a phone or terminal without needing a custom client, but the binary protocol is the primary integration path going forward.

### Overview commands

| Command | Description |
|---------|-------------|
| `wx` | National overview |
| `wx TX` or just `TX` | State overview |
| `wx Austin TX` | City-level conditions, observations, forecast |
| `help` | List commands |
| `more` | Next page of a truncated response |

### Detailed commands

| Command | Example | Description |
|---------|---------|-------------|
| `forecast <city ST>` | `forecast Miami FL` | Zone forecast or discussion summary |
| `warn` / `warn <ST>` / `warn <city ST>` | `warn KS` | Warning listing at various granularities |
| `outlook <city ST>` | `outlook Des Moines IA` | 1-7 day hazardous weather outlook |
| `rain` / `rain <ST>` | `rain FL` | Areas reporting rain |
| `storm` / `storm <ST>` | `storm SD` | Local storm reports |
| `metar <ICAO>` | `metar KJFK` | Raw METAR |
| `taf <ICAO>` | `taf KJFK` | Terminal aerodrome forecast text |

Both 3-letter (IATA/FAA) and 4-letter (ICAO) station codes work: `wx AUS` = Austin-Bergstrom, `wx KJFK` = JFK NYC, `wx SJU` = San Juan PR.

### Hybrid DM/channel transport

The bot uses a channel-with-DM-fallback routing system to keep channel spam low:

1. New users send commands on the channel and get a few free replies plus a prompt to send an advert
2. When a user adverts, the bot detects it, re-adverts itself, and sends a DM welcome
3. After that, responses go DM-first automatically
4. If DMs break (user deleted the bot contact), the bot detects the failure and falls back to channel with a nudge to re-advert

Text commands have a 5-second per-user rate limit. Binary data requests (`WXQ` / `MWX` prefixed DMs) bypass this because they have their own per-`(data_type, location)` 5-minute rate limit at the broadcaster level.

### Admin commands

Authenticated by `MCW_ADMIN_KEY` (pubkey prefix), available via DM only:

| Command | Description |
|---------|-------------|
| `admin` | Show admin help |
| `contacts` | List all known contacts |
| `remove <name>` | Remove a specific contact |
| `clear-contacts` | Remove ALL contacts from the device |
| `advert` | Send a flood advert + refresh contacts |
| `refresh` | Reload contacts from the device |

## Architecture

```
meshcore_weather/
├── config.py              # Settings loaded from env vars
├── main.py                # Entry point, DM/channel routing, command dispatch
├── nlp.py                 # Typo-tolerant text command parser
├── cli.py                 # CLI helpers for testing + radio admin
│
├── emwin/
│   └── fetcher.py         # EMWIN ingestion (internet now, SDR stubbed)
│
├── parser/
│   ├── weather.py         # NWS text product parsing + text-command queries
│   └── pfm.py             # PFM column-position parser + daily downsampler
│
├── protocol/              # MeshWX v3 binary wire format
│   ├── meshwx.py          # pack/unpack for every message type, COBS, state index
│   ├── encoders.py        # Product text → binary (encode_* helpers)
│   ├── coverage.py        # Operator coverage (cities/states/WFOs → zone set)
│   ├── warnings.py        # pyIEM-backed warning extraction
│   ├── radar.py           # IEM NEXRAD composite → 16×16 grids per region
│   └── broadcaster.py     # Reactive: responds to 0x02 DM data requests
│
├── schedule/              # Unified broadcast schedule system
│   ├── models.py          # BroadcastJob, BroadcastConfig (pydantic)
│   ├── store.py           # Atomic JSON persistence + env-var bootstrap
│   ├── executor.py        # Product → builder registry (data-driven)
│   └── scheduler.py       # Tick loop, per-job intervals, radio transmission
│
├── portal/                # FastAPI + Jinja2 web admin
│   ├── server.py
│   ├── routes/
│   │   ├── pages.py       # HTML endpoints (index, schedule, config, etc.)
│   │   ├── api.py         # JSON API (schedule CRUD, warnings, products, actions)
│   │   └── emwin.py       # Product browser
│   ├── templates/         # Jinja2 templates
│   └── static/            # CSS + bundled vendor libs (MapLibre, HTMX)
│
├── client_data/           # Preload bundle shipped to clients (package-data)
│   ├── zones.json         # 4029 NWS forecast zones
│   ├── places.json        # 32333 US Census places
│   ├── stations.json      # 2237 METAR stations
│   ├── wfos.json          # 125 NWS Weather Forecast Offices
│   ├── state_index.json   # State/marine prefix → 1-byte index
│   ├── protocol.json      # Protocol version + enum reference
│   ├── pfm_points.json    # 1873 PFM forecast points (name, WFO, lat/lon, zone)
│   ├── zones.geojson      # 4047 simplified zone polygons for map rendering
│   └── weather_dict.json  # Reserved for future dict text compression
│
├── geodata/               # Source data for the client_data bundle
│   └── *.json
│
└── meshcore/
    └── radio.py           # Meshcore radio interface: channels, DMs, adverts
```

## Docs

- `docs/MeshWX_Protocol_v3.md` — canonical binary wire format spec
- `docs/iOS_Developer_Brief.md` — integration guide for iOS / web / embedded client developers
- `docs/Future_EInk_Dashboard.md` — parked project idea for a standalone e-ink hardware display

## Safety

- **Channel isolation**: the bot will never transmit on channel 0 (public) or any channel other than its configured ones. Enforced at both the message handler and radio driver layers.
- **Text-command rate limit**: 5 seconds per user for text DMs (human-user protection)
- **Binary-request rate limit**: 5 minutes per `(data_type, location)` tuple on the broadcaster (multi-client broadcast amortization)
- **DM fallback**: if DMs fail, the bot detects it and falls back to channel responses gracefully
- **Channel spam limits**: unknown contacts get a small number of free channel replies then must advert
- **Input sanitization**: text commands are length-limited and stripped of control characters
- **Admin authentication**: admin commands require matching `MCW_ADMIN_KEY` pubkey prefix — cannot be spoofed via channel

## Roadmap

Shipped:

- [x] Internet-based EMWIN data fetching with disk cache
- [x] pyIEM canonical NWS product parsing (VTEC, UGC, polygons)
- [x] MeshWX v3 binary protocol (10 message types, all wired)
- [x] COBS-encoded wire format (survives firmware null-byte truncation)
- [x] Absolute Unix-minute expiry timestamps (no client-side countdown drift)
- [x] PFM forecast source (structured numeric data, displacing ZFP narrative regex)
- [x] Unified per-job broadcast schedule system (any product, any location, any interval)
- [x] Web admin portal with schedule management + CRUD API
- [x] Preload bundle (`client_data/`) with 1873 PFM points, 4047 zone polygons, 32333 places
- [x] Data request reactive path (`WXQ` DM + broadcast response)
- [x] Legacy text-command interface with typo-tolerant parser
- [x] Hybrid DM/channel routing with admin commands
- [x] Docker container with serial passthrough

In progress:

- [ ] iOS client (third party)

Planned:

- [ ] GOES-E SDR satellite downlink via goesrecv/goestools
- [ ] Proactive warning push (immediate broadcast when NEW warnings land in the fetch cycle, not waiting for next scheduled tick)
- [ ] County FIPS (`TXC###`) support in `0x21` warning messages
- [ ] Full VTEC action/ETN/office/urgency/certainty fields on the wire
- [ ] H-VTEC hydrologic metadata (flood severity, river ID, stage forecast)
- [ ] Dictionary text compression for warning headlines
- [ ] 3-hourly hour-by-hour PFM forecast format
- [ ] Standalone e-ink weather display hardware product (see `docs/Future_EInk_Dashboard.md`)

## License

MIT
