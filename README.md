# Meshcore Weather

**Off-grid weather data infrastructure for [Meshcore](https://meshcore.co) LoRa mesh networks.** Fetches NWS EMWIN weather products (forecasts, warnings, observations, storm reports, etc.), parses them with canonical NWS tooling, and broadcasts them on a LoRa mesh channel as compact structured binary messages that any subscribed client — phone apps, web clients, standalone hardware displays — can decode offline, without the internet.

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  GOES-16     │     │                 │     │  #aus-meshwx-v4  │     ┌─────────────┐
│  (future SDR)├────►│  meshcore-      ├────►│  LoRa channel    ├────►│  iOS app    │
└──────────────┘     │  weather        │     │                  │     └─────────────┘
                     │                 │     │  0x21 Zone Warn  │     ┌─────────────┐
┌──────────────┐     │  • fetch        │     │  0x3E Space Wx   ├────►│  Web client │
│  NOAA EMWIN  │     │  • parse (pyIEM)│     │  0x20 Warning    │     └─────────────┘
│  internet    ├────►│  • schedule     │     │  0x30 Obs        │     ┌─────────────┐
└──────────────┘     │  • broadcast    │     │  0x31 Forecast   ├────►│  E-ink      │
                     └────────┬────────┘     │  0x38 Fire Wx    │     │  dashboard  │
                              │              │  0x3C Nowcast     │     │  (future)   │
                              ▼              │  0xF0 Beacon      │
                    ┌─────────────────┐      └──────────────────┘
                    │ Web admin portal│
                    │ localhost:8080  │
                    └─────────────────┘
```

## What this gives you

- **A working operator node** that can run on a Raspberry Pi or any Linux/macOS box with a LoRa serial radio attached. Docker-compose one-liner.
- **MeshWX v5**, a compact binary protocol for apps: warnings with storm tags, polygons and county/zone runs, an active-warning digest for loss recovery, batched observations and point forecasts, as MeshCore `GRP_DATA` packets on `#meshwx`. The mesh carries identifiers and numbers; the phone carries the tables. Spec: `docs/MeshWX_v5_Spec.md`.
- **Discovery by advert** — a bot adverts as a chat node named `WX-<IATA>` (e.g. `WX-AUS`) with its lat/lon, so every MeshCore app already collects what it needs to list nearby weather bots. No discovery channel, no beacon, no extra airtime.
- **A per-job broadcast schedule system** with a web admin UI. Operators define arbitrary `(product, location, interval)` jobs via the portal — e.g. "Austin METAR every 30 min", "TX storm reports every 10 min", "EWX outlook every 12 hr". Jobs persist across restarts.
- **Preload bundle** (`client_data/`, ~9.9 MB) that ships with every client app — NWS zones, census places, METAR stations, WFO metadata, PFM forecast points, zone polygons. With this preloaded, broadcasts only carry compact IDs instead of full names, slashing airtime.
- **pyIEM-powered parsing** — the reference Python library for NWS text products (VTEC, UGC, CAP standards). Runs fully offline with a `legacy_dict` UGC provider built from bundled zones data.
- **Canonical NWS data quality**: forecasts from PFM (Point Forecast Matrix) tables, warnings with correct VTEC extraction and polygon winding, absolute expiry timestamps so clients always know exactly when data becomes invalid.
- **One request grammar for apps and people.** An app DMs `>f 102` and gets a binary answer on the channel for everyone; a person DMs `forecast austin tx` and gets text back. Same words, one bot.
- **Legacy text-command interface** that lets a human user on the mesh DM the bot in plain English (`wx austin`, `forecast dallas tx`, `warn OK`) and get text replies. Secondary to the binary protocol but still works.

## Status

| Area | State |
|---|---|
| EMWIN data ingestion (internet) | ✅ production |
| EMWIN data ingestion (GOES SDR) | ⏳ stubbed, pending SDR hookup |
| pyIEM canonical product parsing | ✅ shipped |
| MeshWX v5 wire format (warning, cancel, digest, observations, forecast, text) | ✅ shipped |
| Echo tracking and byte-identical resend when the mesh did not repeat us | ✅ shipped |
| Discovery by advert (`WX-<IATA>` chat node) | ✅ shipped |
| Broadcast schedule system + web portal UI | ✅ shipped |
| iOS client | 🔨 building against v5 |
| Text-command interface | ✅ shipped (legacy) |
| Standalone e-ink dashboard (consumer) | 💡 idea parked in `docs/Future_EInk_Dashboard.md` |

## Wire format at a glance

Every message is one MeshCore `GRP_DATA` packet (`data_type 0xFF10`) on the
`#meshwx` channel, at most 165 bytes, with a 4-byte header: sequence number,
two bytes of the bot's public key, message type. Little-endian throughout.

| Type | Message | Size | When |
|---|---|---|---|
| 1 | Warning: VTEC event, office, ETN, absolute expiry, storm tags, polygon and/or zone or county runs | 15 to ~110 B | on change; life-safety warnings once more after 90 s |
| 2 | Cancel | 8 B | when a warning ends before its expiry |
| 3 | Digest: every active identity with its expiry, plus feed health | 10 + 6 per warning | every 3 h, after a cancel, on request |
| 4 | Observations: up to 14 stations in one packet | 9 + 10 per station | hourly, on request |
| 5 | Forecast: 7 daily periods for a PFM point | 12 + 5 per period | every 6 h for the home point, on request |
| 6 | Text: warning narrative, forecast discussion, storm reports, METAR/TAF, outlook, space weather | chunked | on request only |
| 7 | Not available | 6 B | answer to a request the bot cannot serve |

Requests are DMs prefixed with `>` (`>d`, `>w`, `>w SV.W.EWX.42`, `>o KAUS`,
`>f 102`, `>afd EWX`). The full byte layouts, the preload bundle, rendering
guidance and test vectors: **`docs/MeshWX_v5_Spec.md`** and
`docs/meshwx_v5_vectors.json`. Reference codec: `meshcore_weather/protocol/v5.py`.

## For client developers (iOS, web, embedded)

Start and finish with **`docs/MeshWX_v5_Spec.md`**. Decode the test vectors, ship
the `client_data/` bundle, follow the request rules. The v3/v4 documents are gone;
nothing from them decodes as v5.

## For operators

### Configure your coverage once via `.env`

```bash
MCW_HOME_CITIES=Austin TX,San Antonio TX        # Cities to broadcast obs+forecast for
MCW_HOME_STATES=TX                              # States for warning filtering
MCW_HOME_WFOS=EWX,FWD,HGX,SJT                   # NWS offices — narrows warnings
```

Coverage determines which warnings get filtered to your area, and which home cities get proactive obs/forecast broadcasts. On first run, the bot synthesizes a default broadcast schedule from your coverage config.

### Then manage everything else from the admin portal

The portal (`MCW_PORTAL_PORT`, 8081 on the Pi) is one page with six sections.
Each setting lives in exactly one place, next to the status it affects:

| Section | What it shows | What you set there |
|---|---|---|
| **Overview** | Dish lock, feed age, radio link, transmit and reply mode, answers in the last hour, jobs, log problems, audit result, host. Refreshes every 15 s; the header strip repeats the three that matter on every page. | nothing |
| **Text Bot** | Request/reply counters, the live feed of the channel and DMs (with why a request was not answered), a "try a command" box that runs the DM path, the `help` text | reply mode (with a confirmation before `channel`), stranger hop limit, advert interval, peer-bot prefix |
| **Broadcasts** | Jobs with last/next run and bytes, data-channel counters, the broadcast log (jobs, app requests, beacons) | jobs (add, edit, enable, run now, delete), "run due jobs" |
| **Radio** | Link, node, LoRa parameters, battery, all 8 channel slots, the contact table with housekeeping status | node name and location, LoRa preset or parameters, TX power, transmit on/off, the three channel names, contact housekeeping |
| **Satellite** | goesrecv lock and signal history, goesproc, what the EMWIN feed delivered, a browser for every product in the store | pointing / receive mode |
| **System** | Logs (satellite, radio, bot; live, filterable), host stats | coverage (cities, radius, states, offices), serial port, EMWIN source and directory, timezone, log level, restart |

Settings are written to `.env` and applied live where the bot can (the
response says which keys need a restart). Values are validated before the
file is touched. The schedule persists in `data/broadcast_config.json`.
The portal has no login: keep it on the LAN or gate it at the edge.

### Example schedule you might configure

```
ID                Name                       Product        Location       Interval
─────────────────────────────────────────────────────────────────────────────────
warnings-coverage Active TX warnings         warnings       coverage       5 min
obs-austin-tx     Austin current wx          observation    city:Austin TX 30 min
obs-san-antonio   San Antonio current wx     observation    city:San...    30 min
forecast-austin   Austin 7-day forecast      forecast       city:Austin TX 2 hr
forecast-sa       San Antonio 7-day          forecast       city:San...    2 hr
ewx-hwo           EWX hazardous outlook      outlook        city:Austin TX 12 hr
tx-storm-reports  TX storm reports           storm_reports  city:Austin TX 10 min
kaus-taf          KAUS TAF snapshot          taf            station:KAUS   60 min
kdfw-taf          KDFW TAF snapshot          taf            station:KDFW   60 min
fire-wx-austin    Austin fire weather        fire_weather   city:Austin TX 6 hr
nowcast-austin    Austin nowcast             nowcast        city:Austin TX 1 hr
```

## Quick start

### With Docker (recommended)

```bash
git clone https://github.com/digitaino/meshwx.git
cd meshwx
cp .env.example .env
# Edit .env with your MCW_SERIAL_PORT, MCW_MESHWX_CHANNEL, MCW_HOME_*, etc.

docker compose up -d
```

The container will:

1. Connect to your configured serial radio (or TCP radio proxy)
2. Start fetching EMWIN data from NOAA every 2 minutes
3. Bootstrap a default broadcast schedule from your `.env` coverage config
4. Launch the admin portal on `http://localhost:8080`
5. Start the broadcast scheduler

### Without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[portal]"
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
INFO radio: Data channel 4 (#aus-meshwx-v4)
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

## Running on the receiver Pi (no Docker)

The production shape is one Raspberry Pi running goestools for the dish and
this bot for the mesh, radio on USB. Docker is not needed there (and a 2 GB Pi
has no room for the image build); a venv is enough:

```bash
git clone https://github.com/digitaino/meshwx.git ~/meshcore-weather
cd ~/meshcore-weather
python3 -m venv .venv && .venv/bin/pip install -e ".[portal]"
cp deploy/pi.env.example .env        # edit: serial port, home city, admin key
sudo cp deploy/meshcore-weather.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meshcore-weather
journalctl -u meshcore-weather -f
```

`MCW_EMWIN_SOURCE=sdr` makes the bot read goesproc's `emwin/YYYY-MM-DD/` tree
directly; nothing is fetched from the internet. The bot starts without a
radio and keeps retrying the serial port every minute, so the Heltec can be
plugged in later. To try text commands from the Pi's shell:

```bash
.venv/bin/meshcore-weather-cli interactive
```

### The public page (port 8080)

The goestools dashboard on the Pi (`deploy/goes-dashboard/`, installed as
`~/goes/dashboard.py` + `dashboard.html`, `goes-dashboard.service`) is the
public, read-only page: receiver stats and imagery, plus a card that explains
the mesh weather bot and how to reach it, its request/reply counters, and a
live feed of what it sees on its channel. That card is fed by the admin
portal's `/api/public/bot` bundle, proxied at `/api/bot` on the same port,
so the portal itself (8081) never has to be exposed. DMs are redacted in the
bundle (command and reply length only). To update the page:

```bash
scp deploy/goes-dashboard/dashboard.* pi:~/goes/ && ssh pi sudo systemctl restart goes-dashboard
```

The admin portal's Text Bot page shows the same feed unredacted (sender names,
DM text, admin and console commands, and why a request was not answered).

## Configuration reference

All settings are environment variables prefixed with `MCW_`. See `.env.example` for the full list. Most commonly adjusted:

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_SERIAL_PORT` | `/dev/cu.usbserial-0001` | Serial port or `tcp://host:port` for a networked radio |
| `MCW_SERIAL_BAUD` | `115200` | Serial baud rate |
| `MCW_TX_ENABLED` | `true` | `false` = receive-only passive observer: suppresses all RF transmission (adverts, channel messages, binary datagrams, DMs). RX and MQTT continue |
| `MCW_MESHCORE_CHANNEL` | `#digitaino-wx-bot` | Channel for text commands (never `0`/public) |
| `MCW_MESHWX_CHANNEL` | *(empty)* | Channel for the binary data datagrams. The same name as `MCW_MESHCORE_CHANNEL` shares one slot |
| `MCW_HOME_CITIES` | *(empty)* | Comma-separated cities to seed the default schedule |
| `MCW_HOME_STATES` | *(empty)* | Comma-separated states for warning filtering |
| `MCW_HOME_WFOS` | *(empty)* | Comma-separated WFOs for coverage filtering |
| `MCW_EMWIN_SOURCE` | `internet` | `internet` or `sdr` (future) |
| `MCW_EMWIN_POLL_INTERVAL` | `120` | EMWIN refresh interval in seconds |
| `MCW_EMWIN_MAX_AGE_HOURS` | `12` | Expire products older than this |
| `MCW_PORTAL_ENABLED` | `false` | Set to `true` to enable the web admin portal |
| `MCW_PORTAL_HOST` | `0.0.0.0` | Portal bind address |
| `MCW_PORTAL_PORT` | `8080` | Portal port |
| `MCW_ADMIN_KEY` | *(empty)* | Pubkey prefix of the admin user for DM admin commands |
| `MCW_LOG_LEVEL` | `INFO` | Log level |

Once the bot is running, **the broadcast schedule is managed via `data/broadcast_config.json` and the portal**, NOT via environment variables. Env vars are bootstrap config only.

## Data sources

Every message broadcast is derived from an official NWS product ingested via EMWIN. Parsing is done by [pyIEM](https://github.com/akrherz/pyIEM) wherever possible, with custom parsers where pyIEM doesn't cover a specific product (notably the PFM column-position parser in `parser/pfm.py`).

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
| **FWF** | Fire Weather Forecast | `0x38` Fire Weather (wind, RH, temp, Haines, lightning) |
| **NOW** | Short Term Forecast | `0x3C` Nowcast (urgency flags, text) |
| **RTP** | Regional Temp/Precip | `0x3A` Daily Climate (high/low/precip/snow) |
| **SVR/SVS/TOR/FFW/FLW/FLS/WSW/NPW/RFW/MWW/SPS/...** | NWS warnings | `0x20`/`0x21` Warning broadcasts with VTEC metadata |

Warnings include canonical VTEC event tracking (phenomenon / significance / action / ETN / office), correct polygon winding, both zone (`TXZ192`) and county FIPS (`TXC029`) UGC support, and absolute expiry timestamps so clients never display stale warnings.

## Text-command interface (legacy)

The bot also supports a human-friendly text command interface via channel messages or DMs. This is the **original** interface and predates the binary protocol. It still works and is useful for debugging the data pipeline from a phone or terminal without needing a custom client, but the binary protocol is the primary integration path going forward.

### Overview commands

| Command | Description |
|---------|-------------|
| `wx` | National overview |
| `wx TX` or just `TX` | State overview |
| `wx Austin TX` | City-level conditions, observations, forecast |
| `help` | List commands |
| `more` | Next page of a long reply. A long reply is cut into numbered pages on item boundaries; page 1 ends with `(1/3) more`, and each `more` sends the next page on whichever transport it arrives |

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

Text commands have a 5-second per-user rate limit. App requests (`>` prefixed DMs) have their own 5-second per-sender limit and an hourly budget on the responder.

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
├── config.py              # Settings loaded from env vars (pydantic-settings)
├── main.py                # Entry point, DM/channel routing, command dispatch
├── nlp.py                 # Typo-tolerant text command parser
├── activity.py            # Broadcast log (data-channel events) for the portal
├── cli.py                 # CLI helpers for testing + radio admin
│
├── emwin/
│   └── fetcher.py         # EMWIN ingestion (internet now, SDR stubbed)
│
├── parser/
│   ├── weather.py         # NWS text product parsing + text-command queries
│   └── pfm.py             # PFM column-position parser + daily downsampler
│
├── protocol/
│   ├── v5.py              # MeshWX v5 codec (stdlib only; the reference decoder)
│   ├── v5_builders.py     # store data -> v5 messages (one implementation for jobs and requests)
│   ├── broadcaster.py     # AppResponder: `>` requests, owns the Scheduler
│   ├── coverage.py        # Operator coverage (centre + radius, states, WFOs -> zone set)
│   ├── warnings.py        # pyIEM-backed warning extraction with storm tags
│   ├── meshwx.py, encoders.py, fec.py   # v3/v4 era: kept for the EMWIN parsers the text bot uses
│
├── schedule/              # Unified broadcast schedule system
│   ├── models.py          # BroadcastJob, BroadcastConfig (pydantic)
│   ├── store.py           # Atomic JSON persistence + env-var bootstrap
│   ├── executor.py        # Product → builder registry (data-driven)
│   └── scheduler.py       # Tick loop, per-job intervals, radio transmission
│
├── portal/                # FastAPI admin portal (one page, hash routing, no build step)
│   ├── server.py          # app factory, mutation header check, uvicorn lifecycle
│   ├── sse.py             # SSE helper with heartbeats (logs, traffic, broadcast log)
│   ├── logbuf.py          # log ring buffer behind System > Logs
│   ├── routes/
│   │   ├── pages.py       # GET / (the page)
│   │   ├── api.py         # products, broadcast log, channels, schedule CRUD
│   │   └── admin.py       # overview, radio, satellite, console, traffic, settings, audit
│   ├── templates/app.html
│   └── static/            # portal.js + portal.css, nothing vendored
│
├── client_data/           # Preload bundle shipped to clients (package-data)
│   ├── zones.json         # NWS forecast zones
│   ├── places.json        # US Census places
│   ├── stations.json      # METAR stations
│   ├── wfos.json          # NWS Weather Forecast Offices
│   ├── state_index.json   # State/marine prefix → 1-byte index
│   ├── protocol.json      # Protocol version + enum reference
│   ├── pfm_points.json    # PFM forecast points (name, WFO, lat/lon, zone)
│   ├── regions.json       # MeshWX region definitions + bounds (coverage/beacon)
│   ├── zones.geojson      # Simplified zone polygons for map rendering
│   └── weather_dict.json  # Reserved for future dict text compression
│
├── geodata/               # Source data for the client_data bundle
│   └── *.json
│
└── meshcore/
    └── radio.py           # Meshcore radio interface: channels, DMs, adverts
```

## Docs

- `docs/MeshWX_v5_Spec.md` — the protocol and the app developer's guide (wire, bundle, rendering, requests)
- `docs/meshwx_v5_vectors.json` — test vectors every client must pass
- `docs/MeshWX_Airtime_Review.md` — the review that led to v5, with the airtime numbers
- `docs/Delivery_Confirmation_Design.md` — echo tracking and resend
- `docs/Admin_Portal_Review_2026-09-14.md` — the portal revamp record
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
- [x] MeshWX v5: GRP_DATA transport, warning/cancel/digest/observations/forecast/text, `>` request grammar
- [x] Discovery by advert (`WX-<IATA>` chat node with lat/lon)
- [x] Echo tracking: byte-identical resend when no repeater repeated us
- [x] Absolute Unix-minute expiry timestamps (no client-side countdown drift)
- [x] PFM forecast source (structured numeric data, displacing ZFP narrative regex)
- [x] Fire weather forecasts (FWF → 0x38)
- [x] Nowcasts (NOW → 0x3C) with urgency flags
- [x] Daily climate summaries (RTP → 0x3A)
- [x] QPF precipitation grids (0x12)
- [x] Unified per-job broadcast schedule system (any product, any location, any interval)
- [x] Admin portal: overview, text bot feed and console, broadcasts, radio, satellite, logs and settings
- [x] Preload bundle (`client_data/`) with PFM points, zone polygons, places, stations
- [x] App requests answered on the channel so one request serves every listener
- [x] Legacy text-command interface with typo-tolerant parser
- [x] Hybrid DM/channel routing with admin commands
- [x] Docker container with serial passthrough
- [ ] iOS client against v5

Planned:

- [ ] GOES-E SDR satellite downlink via goesrecv/goestools
- [ ] County polygons in the preload bundle
- [ ] H-VTEC hydrologic metadata (flood severity, river ID, stage forecast)
- [ ] Dictionary text compression for warning headlines
- [ ] 3-hourly hour-by-hour PFM forecast format
- [ ] Standalone e-ink weather display hardware product (see `docs/Future_EInk_Dashboard.md`)

## License

Apache License 2.0. Copyright 2026 Rafael Pesquera. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Authors

Created and maintained by Rafael Pesquera ([@digitaino](https://github.com/digitaino)). Contributions from the Austin mesh community are welcome; see the open protocol docs under `docs/`.

## Related

- **[meshwx-client](https://github.com/digitaino/meshwx-client)** — Desktop & web client for receiving and displaying MeshWX weather data. Connects via USB or Bluetooth.
- **[DigitainoMesh](https://github.com/digitaino/DigitainoMesh)** — iOS MeshCore client with built-in MeshWX weather decoding.
