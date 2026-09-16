# Meshcore Weather

**Off-grid weather for [Meshcore](https://meshcore.co) LoRa mesh networks.** The bot takes NWS EMWIN products (warnings, observations, forecasts, storm reports and more), parses them with canonical NWS tooling, and puts them on a MeshCore channel two ways: compact MeshWX v5 binary datagrams that apps decode offline, and plain-text replies for anyone who sends it a command. In production the products come from a GOES-19 dish received by goestools on a Raspberry Pi, so nothing depends on the internet; NOAA's internet EMWIN feed is the alternative for a bot without a dish.

```
GOES-19 dish ─ goesrecv ─ goesproc ─┐                          ┌─► MeshWX apps: v5 datagrams
  (the Pi, production)              │    ┌──────────────────┐  │   (GRP_DATA on #meshwx)
                                    ├───►│ meshcore-weather │──┤
NOAA EMWIN over the internet ───────┘    │ parse, schedule, │  │
  (the alternative)                      │ answer requests  │  └─► people: text replies
                                         └────────┬─────────┘      (#meshwx and DMs)
                                                  │
                                   admin portal, public dashboard
```

## What this gives you

- **An operator node.** In production: a Raspberry Pi 4 (2 GB) running goestools for the dish and this bot for the mesh, with a MeshCore companion radio on USB, under systemd. Any Linux or macOS box with internet EMWIN works for development.
- **MeshWX v5 for apps.** Warnings with storm tags, polygons and county/zone runs, cancels, an active-warning digest for loss recovery, batched observations, point forecasts, text and "not available", each one MeshCore `GRP_DATA` packet on `#meshwx`. The mesh carries identifiers and numbers; the phone carries the tables. Spec: [`docs/MeshWX_v5_Spec.md`](docs/MeshWX_v5_Spec.md).
- **Text replies for people.** Anyone on `#meshwx` can send `wx austin tx`, `warn TX` or `sat`, on the channel or by DM, and get a text reply, by DM where the bot can reach them. Long replies are paged with `more`. This is how people without the app use the bot.
- **One request grammar for apps and people.** An app sends `>f 102` and gets a binary answer on the channel for every listener; a person sends `forecast austin tx` and gets text.
- **Delivery confirmation.** The radio hears a repeater's copy of each channel packet the bot sends. When none comes back within the echo window, the packet goes out once more, byte for byte the same, so nobody sees it twice. DM replies wait for the recipient's ACK instead: about 2 s after the request, one at a time per person, one timestamp for all tries (attempts 0 and 1 on the route, then one by flood). A phone's resend of a request is not answered twice.
- **Discovery by advert.** The node is named `WX-<city>` (e.g. `WX-AUS`); apps list adverts whose name starts with `WX-`. No discovery channel, no beacon.
- **A broadcast schedule** of four v5 jobs (warnings on change, digest, observations, home forecast), edited in the portal and kept in `data/broadcast_config.json`.
- **Radio swaps.** The bot keeps a profile of its node (identity key, name, position, LoRa settings, contacts) and can write it onto a replacement radio, asking first by default. See [`docs/Radio_Swap.md`](docs/Radio_Swap.md).
- **Receiver status on the mesh.** `sat` (or `>sat` from an app) answers with the dish's lock, signal quality, packets dropped in the last minute and the age of the newest EMWIN file.
- **An admin portal** for the radio, receiver, text bot, schedule, logs and settings, and a **public dashboard** on the Pi that shows the receiver and a redacted live feed of the bot.
- **An accuracy audit.** `scripts/audit.py` compares the bot's answers with api.weather.gov, IEM, aviationweather.gov and SWPC; `deploy/` has an hourly timer for it and the portal's Overview shows the result.
- **CoreScope hooks.** Optional MQTT publishing of every raw packet the radio hears, for the CoreScope packet analyzer (`corescope/`), and an optional CoreScope lookup that records who heard each packet and can veto a resend.
- **Preload bundle** (`client_data/`, about 18 MB, 15 MB of it the optional zone and county polygons) that ships with every app: the office, station and state index tables, zones, counties, places, US ZIP codes, METAR stations, PFM forecast points and the protocol enums.
- **pyIEM-powered parsing** — the reference Python library for NWS text products (VTEC, UGC, polygons), run fully offline with a UGC provider built from the bundled zones.

## Status

| Area | State |
|---|---|
| EMWIN from the GOES-19 dish (goestools, `MCW_EMWIN_SOURCE=sdr`) | production |
| EMWIN over the internet (`MCW_EMWIN_SOURCE=internet`) | works; the alternative for a bot without a dish |
| pyIEM product parsing | shipped |
| MeshWX v5 wire format, spec revision 5 | shipped |
| Text replies for people, paged with `more` | shipped |
| Echo tracking and one byte-identical resend | shipped |
| DM replies: ACK tries, one reply at a time per person, resends of a request recognised | shipped |
| Radio swap: node profile, adoption, health verdict | shipped |
| Receiver status (`sat`, `>sat`) | shipped |
| Coverage statement (`cov`, `>cov`, broadcast every 3 h) | shipped |
| Admin portal and public dashboard | shipped |
| Accuracy audit (`scripts/audit.py`) | shipped |
| MQTT packet publishing for CoreScope | shipped, off by default |
| iOS client | building against v5 |
| Standalone e-ink dashboard | idea parked in `docs/Future_EInk_Dashboard.md` |

## Wire format at a glance

Every message is one MeshCore `GRP_DATA` packet (`data_type 0xFF10`) on
`#meshwx`, at most 165 bytes, with a 4-byte header: sequence number, the
first two bytes of the bot's public key, and the message type (high nibble)
with flags (low nibble). Little-endian throughout. The sequence number is
assigned when a packet is transmitted, from one counter shared by broadcasts
and answers and saved across restarts; a resend repeats the same bytes and
the same number.

| Type | Message | Size | When |
|---|---|---|---|
| 1 | Warning: VTEC event, office, ETN, absolute expiry, storm tags, polygon and/or county or zone runs | 15 B + polygon + runs (a typical storm warning is 51 B) | on change; tornado, severe thunderstorm, flash flood and extreme wind warnings once more after 90 s |
| 2 | Cancel | 8 B | when a warning ends before its expiry |
| 3 | Digest: up to 25 active identities with their expiry, plus feed health | 10 B + 6 per warning | every 3 h, a minute after a cancel, on request |
| 4 | Observations: up to 13 stations in one packet, each with its own age (14 without) | 9 B + 11 per station | hourly, on request |
| 5 | Forecast: up to 7 whole days for a PFM point | 12 B + 5 per day | every 6 h for the home point, on request |
| 6 | Text: warning narrative, forecast discussion, space weather, storm reports, rainfall, METAR/TAF, outlook, receiver status | up to 8 chunks of 157 B of text | on request only |
| 7 | Not available | 6 B | answer to a request the bot cannot serve |

Requests start with `>` and reach the bot as a DM or as text on `#meshwx`;
the answer always comes back on `#meshwx` as v5 messages.

| Request | Answer |
|---|---|
| `>d` | Digest |
| `>w` | Warnings in coverage (at most 6, newest first), then a Digest |
| `>w SV.W.EWX.42`, `>w TXC453`, `>w TXZ192` | One warning by identity, or the warnings touching a county or zone |
| `>wt SV.W.EWX.42` | That warning's narrative as Text |
| `>o`, `>o KAUS` | Observations for the coverage stations, or for one station |
| `>f`, `>f 102`, `>f round rock tx`, `>f 78701` | Forecast for the home point, a PFM point index (1-4 digits), a place, or a ZIP (5 digits or ZIP+4) |
| `>afd EWX` | Forecast discussion |
| `>metar KAUS`, `>taf KAUS` | That station's own report, or Not available; a place or ZIP gets the nearest reporting station |
| `>space`, `>storm TX`, `>rain TX`, `>hwo` | Space weather, storm reports, rainfall, hazardous weather outlook |
| `>sat` | The bot's GOES receiver, one line of Text |
| `>cov` | What this bot covers: centre, radius, NWS offices, zone runs. One packet, also broadcast every 3 h |

Limits: one request per sender every 5 s and 60 answer packets per hour
across all senders; a `>` sent as a DM also counts against the text-command
limits. A throttled request gets no reply.

The full byte layouts, the preload bundle, rendering guidance and these
rules in detail: **[`docs/MeshWX_v5_Spec.md`](docs/MeshWX_v5_Spec.md)**
(revision 5) and [`docs/meshwx_v5_vectors.json`](docs/meshwx_v5_vectors.json).
Reference codec: [`meshcore_weather/protocol/v5.py`](meshcore_weather/protocol/v5.py).

## For client developers (iOS, web, embedded)

Start and finish with **`docs/MeshWX_v5_Spec.md`**. If you built against
revision 2, read its section 16 first: the wire layout did not change, the
bundle did. Decode the test vectors, ship the `client_data/` bundle, follow
the request rules.

| Bundle file | Contents (spec section 9) |
|---|---|
| `protocol.json` | Version, message types, event codes and names, sky codes, text subjects, Not available reasons |
| `index.json` | The tables the wire indexes into: `offices` (the 125 WFOs in alphabetical order, then `NHC` at 125 and `WNS` at 126), `stations`, `states`. Append-only; `version` 2 |
| `stations.json`, `pfm_points.json`, `places.json` | Station, forecast point and place lookup |
| `zips.json` | US ZIP (Census 2020 ZCTA) to its point and nearest place; the bot resolves ZIPs from the same table |
| `zones.json`, `counties.json` | Names and centroids for zone and county codes |
| `zones.geojson`, `counties.geojson` | Polygons; optional downloads |
| `wfos.json` | Office states and positions, in `index.json` `offices` order |
| `regions.json`, `state_index.json`, `weather_dict.json` | v3/v4 leftovers, not used by v5 |

`index.json` and the test vectors are generated by `scripts/v5_vectors.py`;
never edit them by hand. The v3/v4 documents are gone; nothing from them
decodes as v5.

## For operators

### Configure your coverage once via `.env`

```bash
# The first city is the home point: the radius centre and the home forecast.
MCW_HOME_CITIES=Austin TX
# Every forecast zone within this many km of the home point.
MCW_HOME_RADIUS_KM=120
# Optional: whole states and whole NWS offices on top.
MCW_HOME_STATES=TX,OK
MCW_HOME_WFOS=EWX,FWD
```

Coverage decides which warnings the scheduled broadcasts and the bare `>w`
carry; the observation stations are the ones within the radius of the home
point. Commands and requests that name a place are answered nationwide. With
no coverage set at all, broadcasts carry every warning.

### The default schedule

On first start the bot writes `data/broadcast_config.json` with four jobs.
Only these four products can be scheduled; everything narrative is
request-only.

| Job | Every | Sends |
|---|---|---|
| `warnings` | 2 min | New and changed warnings in coverage, a Cancel for one that ended early, and one repeat of a tornado, severe thunderstorm, flash flood or extreme wind warning 90 s later |
| `digest` | 180 min | The active-warning Digest (also a minute after any Cancel) |
| `observations` | 60 min | One packet for up to 13 stations within the radius that reported in the last 120 min |
| `forecast` | 360 min | The forecast for the first home city |

The scheduler checks every 30 s and spaces packets 2 s apart. A job's
location is `coverage`, `city`, `pfm_point` or `station` (observations for
one station). A v4 job file is migrated when it loads: retired products are
dropped and a forecast job runs no more often than every 3 h.

### Then manage everything else from the admin portal

The portal (`MCW_PORTAL_PORT`, 8081 on the Pi) is one page with six sections.
Each setting lives in exactly one place, next to the status it affects:

| Section | What it shows | What you set there |
|---|---|---|
| **Overview** | Dish, feed, radio, transmit and reply mode, requests and replies, jobs, log problems, host; recent requests; the accuracy audit result. Refreshes every 15 s; the header strip repeats dish, radio and transmit on every page. | nothing |
| **Text Bot** | Request/reply counters, the live feed of the channel and DMs (with why a request was not answered), a "try a command" box that runs the DM path, the `help` text, the channels it listens on, peer bots heard | reply mode (with a confirmation before `channel`), stranger hop limit, advert interval, peer-bot prefix, resend limit, echo window, resends per hour |
| **Broadcasts** | Counters, jobs with last/next run and bytes, the broadcast log (jobs and app requests) | jobs (add, edit, enable, run now, delete), "run due jobs" |
| **Radio** | Link, health verdict, hardware and node profile, LoRa parameters, all channel slots, the contact table with housekeeping status | test transmit, adopt a replacement radio or start a new profile, node name and location, LoRa preset or parameters, TX power, transmit on/off, advert, reboot, the text and data channel names, contact housekeeping |
| **Satellite** | goesrecv and goesproc, signal stats and history, what the EMWIN feed delivered in the last hour, a browser for every product in the store, a link to the public dashboard | pointing / receive mode |
| **System** | Logs (satellite, radio, bot; live, filterable), bot and host stats | coverage (cities, radius, states, offices), serial port, EMWIN source and directory, dashboard URL, timezone, log level, CoreScope, replacement-radio mode, receiver-silent threshold, restart |

Settings are written to `.env` and applied live where the bot can (the
response says which keys need a restart). Values are validated before the
file is touched. The portal has no login: keep it on the LAN or gate it at
the edge. Every state-changing request must carry the header
`X-Requested-With: meshcore-portal`, so a web page opened on the LAN cannot
change anything through someone's browser.

## Quick start

### With Docker

```bash
git clone https://github.com/digitaino/meshwx.git
cd meshwx
cp .env.example .env
# Edit .env: MCW_HOME_CITIES, MCW_ADMIN_KEY, the channels
docker compose up -d meshcore-weather
```

`docker-compose.yml` also defines the CoreScope stack (`mosquitto`,
`corescope`, `obs-broker`), which needs its own git-ignored configuration
under `corescope/`; naming the service starts the bot alone. The compose
file points the bot at `tcp://host.docker.internal:4403`, a TCP bridge to
the radio on the host: on macOS `scripts/start.sh` runs socat for it and
then starts the whole compose stack. The portal is on
`http://localhost:8080` when `MCW_PORTAL_ENABLED=true`.

### Without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[portal]"
cp .env.example .env
# Edit .env
meshcore-weather
```

On start the bot loads EMWIN (`internet`: NOAA's 1-hour bundle, then the
2-minute bundle every `MCW_EMWIN_POLL_INTERVAL` seconds; `sdr`: the goesproc
directory), connects to the radio (or retries every minute until one
answers), starts the scheduler once a data channel is up, and starts the
portal if enabled.

### First-run verification

On the first start you should see log lines like these (slot numbers vary):

```
[INFO] meshcore_weather.meshcore.radio: Listening on channel 1 (#meshwx)
[INFO] meshcore_weather.meshcore.radio: Data channel 1 (#meshwx, shared with text)
[INFO] meshcore_weather.schedule.store: Bootstrap schedule: 4 default jobs
[INFO] meshcore_weather.schedule.scheduler: Broadcast scheduler started: 4 jobs, tick every 30s
[INFO] meshcore_weather.portal.server: Portal running at http://0.0.0.0:8080
[INFO] meshcore_weather.main: Weather bot is running. Listening on channel 1 (#meshwx) + DMs
```

Open `http://localhost:8080/#broadcasts` to see the four jobs, and type
`help` or `wx austin tx` into Text Bot > Try a command.

## CLI tools

`meshcore-weather-cli` runs the bot's code without the mesh:

```bash
meshcore-weather-cli fetch              # Load products from the configured EMWIN source and list them
meshcore-weather-cli query "Austin TX"  # The wx reply for a place, from those products (no radio)
meshcore-weather-cli interactive        # Type commands as a person would; "more" pages, "sat" reads the receiver
meshcore-weather-cli sat                # The sat reply: one read of the goestools dashboard, no products loaded
meshcore-weather-cli contacts           # List the contacts on the radio
meshcore-weather-cli remove <name>      # Remove a contact by name
meshcore-weather-cli clear-contacts     # Remove every contact
```

The last three open the radio themselves; stop the bot first.

## Running on the receiver Pi (no Docker)

The production shape is one Raspberry Pi running goestools for the dish and
this bot for the mesh, radio on USB. Docker is not needed there (and a 2 GB Pi
has no room for the image build); a venv is enough:

```bash
git clone https://github.com/digitaino/meshwx.git ~/meshcore-weather
cd ~/meshcore-weather
python3 -m venv .venv && .venv/bin/pip install -e ".[portal]"
cp deploy/pi.env.example .env        # edit: serial port, home city, admin key, then MCW_TX_ENABLED
sudo cp deploy/meshcore-weather.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meshcore-weather
journalctl -u meshcore-weather -f
```

The unit runs as `digitaino` from `/home/digitaino/meshcore-weather`; change
`User`, `WorkingDirectory`, `EnvironmentFile` and `ExecStart` for another
account. systemd reads `.env` as well, and it does not strip comments after
a value, so keep comments on their own lines.

`MCW_EMWIN_SOURCE=sdr` makes the bot read goesproc's `emwin/YYYY-MM-DD/` tree
directly; no EMWIN is fetched from the internet. `MCW_SDR_DASHBOARD_URL`
(`http://127.0.0.1:8080`) is the goestools dashboard that the `sat` reply,
the receiver log lines and the Satellite page read. The bot starts without a
radio and keeps retrying every minute, trying `/dev/meshcore` and every USB
serial port when the configured one does not answer, so the radio can be
plugged in later. To try text commands from the Pi's shell:

```bash
.venv/bin/meshcore-weather-cli interactive
.venv/bin/meshcore-weather-cli sat      # GOES receiver status, as the sat command replies
```

The rest of `deploy/`:

| File | What it is |
|---|---|
| `99-meshcore-radio.rules` | udev rule: `/dev/meshcore` for any supported USB radio board |
| `meshcore-weather-audit.service`, `meshcore-weather-audit.timer` | `scripts/audit.py` hourly, results in `data/audit.json` for the Overview |
| `goes-dashboard/` | The public page (below) |
| `goes-cleanup/` | Hourly retention for goestools output: images 1 day, EMWIN and text 3 days, oldest days removed while the disk is over 70 % |

The audit timer is installed like the bot's unit:

```bash
sudo cp deploy/meshcore-weather-audit.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meshcore-weather-audit.timer
```

### Updating the Pi

The Pi's checkout tracks `main` on GitHub over HTTPS (the repository is
public, so the Pi needs no key). Push to GitHub, then:

```bash
ssh digitaino@mesh-wx.digitaino.com meshcore-weather/scripts/pi_update.sh
```

It fetches, refuses to run over tracked files edited on the Pi, stops the
bot, checks out the new commit, reinstalls when `pyproject.toml` changed,
checks that the code imports (and goes back to the old commit if it does
not), and starts the bot again. It prints the commit to roll back to
(`pi_update.sh <commit>`). Files git ignores stay as they are: `.env`,
`data/`, `.venv`, CoreScope's config, passwords and bundles.

### The public page (port 8080)

The goestools dashboard on the Pi (`deploy/goes-dashboard/`, installed as
`~/goes/dashboard.py` + `dashboard.html`, `goes-dashboard.service`) is the
public, read-only page: receiver stats and imagery, plus a card that explains
the mesh weather bot and how to reach it, its request/reply counters, and a
live feed of what it sees on its channel. That card is fed by the admin
portal's `/api/public/bot` bundle (read from `BOT_URL`, default
`http://127.0.0.1:8081`), proxied at `/api/bot` on the same port, so the
portal itself never has to be exposed. DMs are redacted in the bundle
(command and reply length only). To update the page:

```bash
scp deploy/goes-dashboard/dashboard.* digitaino@mesh-wx.digitaino.com:goes/
ssh digitaino@mesh-wx.digitaino.com sudo systemctl restart goes-dashboard
```

The admin portal's Text Bot page shows the same feed unredacted (sender names,
DM text, admin and console commands, and why a request was not answered).

## Configuration reference

All settings are environment variables prefixed with `MCW_`, read from the
environment and from `.env` in the working directory. `.env.example` (dev
box) and `deploy/pi.env.example` (receiver Pi) are starting points.

**Radio and channels**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_SERIAL_PORT` | `/dev/cu.usbserial-0001` | Serial port of the companion radio, or `tcp://host:port` for a radio behind a TCP bridge. When a serial port is missing or silent the bot also tries `/dev/meshcore` and every USB serial port |
| `MCW_SERIAL_BAUD` | `115200` | Serial baud rate |
| `MCW_MESHCORE_CHANNEL` | `#meshwx` | The request channel for people and apps; created on a free slot if the node lacks it. Never slot 0 (public) |
| `MCW_MESHWX_CHANNEL` | `#meshwx` | The data channel for v5 datagrams and `>` answers. The same name as `MCW_MESHCORE_CHANNEL` shares one slot, which is where v5 apps listen; a different name takes a second slot. Empty = no broadcasts and no `>` answers |
| `MCW_TX_ENABLED` | `true` | `false` = receive only: adverts, replies, datagrams and resends become logged no-ops. Receiving, MQTT and the portal continue |
| `MCW_ADVERT_INTERVAL_HOURS` | `6` | Hours between flood adverts |
| `MCW_CONTACT_HOUSEKEEPING` | `true` | Keep the node's contact table for people: store companions only, remove repeaters, rooms and sensors, and the people heard longest ago when free slots run low. The admin and peer bots are never removed. `false` = firmware behaviour |
| `MCW_CONTACT_SLOTS` | `100` | Contact capacity to assume when the firmware does not report one |
| `MCW_CONTACT_KEEP_FREE` | `10` | Slots housekeeping leaves free for newcomers |
| `MCW_RADIO_ADOPT` | `manual` | A different radio on the port: `manual` waits for Adopt in the portal, `auto` writes the saved profile onto it at once, `off` never adopts |
| `MCW_RADIO_RX_SILENT_MIN` | `30` | Minutes with nothing heard before the health verdict calls the receiver silent |

**Text replies**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_REPLY_MODE` | `dm` | `dm`, `dm_only` or `channel` (see "How replies go out") |
| `MCW_CHANNEL_REPLY_MAX_HOPS` | `2` | A sender the bot cannot DM gets a channel reply only within this many hops |
| `MCW_PEER_BOT_PREFIX` | `WX-` | Name prefix of other weather bots; their channel messages are ignored |
| `MCW_ADMIN_KEY` | *(empty)* | Hex public-key prefix allowed to run admin commands by DM |
| `MCW_TIMEZONE` | `America/Chicago` | Time zone for times in text replies; the wire is always UTC |

**Delivery**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_RETRANSMIT_MAX` | `1` | Resends of a channel packet nobody repeated; `0` = measure only, and a DM reply gets attempt 0 only |
| `MCW_ECHO_WINDOW_S` | `8.0` | Seconds to wait for a repeater's echo before resending |
| `MCW_RETRANSMIT_PER_HOUR` | `30` | Resends allowed per hour |
| `MCW_MESH_QUIET_S` | `600` | When no repeat has been heard from anyone for this many seconds, skip the resend |
| `MCW_SCOPE_URL` | *(empty)* | CoreScope instance to ask who heard a packet; empty = off |
| `MCW_SCOPE_MODE` | `stats` | `stats` records the observers; `decide` also skips a resend CoreScope saw repeated |
| `MCW_SCOPE_MIN_OBSERVERS` | `2` | Observers of a repeated copy needed before it counts |
| `MCW_DM_REPLY_DELAY_S` | `2.0` | A DM reply's first try leaves at least this long after its request arrived |
| `MCW_DM_COPY_WINDOW_S` | `120` | A DM with the same sender and text within this many seconds of the first is a copy of that request |
| `MCW_DM_COPY_RETAIN_S` | `1800` | A DM with the same sender, timestamp and text within this many seconds is a copy |

**EMWIN**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_EMWIN_SOURCE` | `internet` | `internet` (NOAA bundles) or `sdr` (the goesproc directory) |
| `MCW_EMWIN_POLL_INTERVAL` | `120` | Seconds between store refreshes; with `internet`, also between bundle downloads |
| `MCW_EMWIN_BASE_URL` | `https://tgftp.nws.noaa.gov/SL.us008001/CU.EMWIN/DF.xt/DC.gsatR/OPS/txthrs01.zip` | Bundle loaded at start (`internet`) |
| `MCW_EMWIN_POLL_URL` | `https://tgftp.nws.noaa.gov/SL.us008001/CU.EMWIN/DF.xt/DC.gsatR/OPS/txtmin02.zip` | Bundle polled after that (`internet`) |
| `MCW_EMWIN_MAX_AGE_HOURS` | `12` | Hours a product stays in the store; warning-class products stay at least 48 h and storm reports at least 24 h |
| `MCW_SDR_EMWIN_DIR` | `~/goes-images/emwin` | goesproc's EMWIN output (`YYYY-MM-DD/` directories) |
| `MCW_SDR_POLL_INTERVAL` | `30` | Seconds between directory scans |
| `MCW_SDR_DASHBOARD_URL` | `http://127.0.0.1:8080` | The goestools dashboard: the `sat` reply, receiver log lines, the portal's Satellite page and Overview |

**Coverage**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_HOME_CITIES` | *(empty)* | Comma-separated places; the first is the home point (radius centre, home forecast, `>f` and `>o` without an argument) |
| `MCW_HOME_RADIUS_KM` | `120` | Every forecast zone within this radius of the home point is covered; `0` = no radius |
| `MCW_HOME_STATES` | *(empty)* | Comma-separated states, covered whole |
| `MCW_HOME_WFOS` | *(empty)* | Comma-separated NWS offices, covered whole |

**Portal**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_PORTAL_ENABLED` | `false` | Run the admin portal (needs the `[portal]` extra) |
| `MCW_PORTAL_HOST` | `0.0.0.0` | Portal bind address |
| `MCW_PORTAL_PORT` | `8080` | Portal port |

**MQTT (CoreScope)**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_MQTT_ENABLED` | `false` | Publish every raw packet the radio hears to an MQTT broker |
| `MCW_MQTT_HOST` | `mosquitto` | Broker host |
| `MCW_MQTT_PORT` | `1883` | Broker port |
| `MCW_MQTT_TOPIC_PREFIX` | `meshcore` | Topics are `<prefix>/<iata>/<public key>/packets` |
| `MCW_MQTT_IATA` | `AUS` | Region code in the topic |
| `MCW_MQTT_USERNAME`, `MCW_MQTT_PASSWORD` | *(empty)* | Broker credentials |
| `MCW_MQTT_ORIGIN` | `meshcore-weather` | Observer name in each published message |

**Other**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCW_DATA_DIR` | `data` | Where the bot keeps its state: schedule, warning state and sequence number, known contacts, node profile, EMWIN cache |
| `MCW_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `MCW_MESHWX_REFRESH_COOLDOWN` | `300` | Not read by the current code (left from v4) |

Once the bot is running, **the broadcast schedule is managed in the portal and `data/broadcast_config.json`**, not in environment variables.

## Data sources

Every message is derived from an official NWS product received over EMWIN. Parsing is done by [pyIEM](https://github.com/akrherz/pyIEM) wherever it covers the product, with custom parsers for the rest (notably the PFM column-position parser in `parser/pfm.py`).

| Product | Used for |
|---|---|
| Warnings, watches and advisories with VTEC (TOR, SVR, SVS, FFW, FLW, FLS, WSW, NPW, RFW, MWW, ...) and SPS | `warn`, `wx`; v5 Warning, Cancel and Digest; `>w`, `>wt` |
| METAR collectives (SAH) | `wx` conditions, `metar`; v5 Observations; `>metar` |
| PFM (Point Forecast Matrix) | `forecast`, `wx`; v5 Forecast |
| TAF | `taf`; `>taf` |
| HWO (Hazardous Weather Outlook) | `outlook`; `>hwo` |
| LSR (Local Storm Reports) | `storm`; `>storm` |
| RWR (Regional Weather Roundup) | `rain`; `>rain` |
| AFD (Area Forecast Discussion) | `>afd` |
| SWPC products (3-Day Forecast, Daily Indices, alerts) | `space`; `>space` |

Warnings carry VTEC event tracking across product segments (`protocol/vtec_events.py`), polygons, county (`TXC453`) and zone (`TXZ192`) codes, and absolute expiry times. Warning-class products stay in the store for 48 h and storm reports for 24 h, so the VTEC expiry and the report time decide what is current (`emwin/retention.py`).

## Text commands for people

Anyone can send these on `#meshwx` or by DM. A message that is not a
command is read as a place: `austin tx` means `wx austin tx`.

| Command | Example | Reply |
|---------|---------|-------|
| `wx <city ST, station or ZIP>` | `wx Austin TX`, `wx KAUS`, `wx AUS`, `wx 78701` | Current conditions, today's high/low, active warnings |
| `wx`, `wx <state>` | `wx TX`, `TX`, `wx texas` | National or state overview |
| `forecast <city ST or ZIP>` | `forecast Miami FL`, `forecast 02134` | Daily forecast from the nearest PFM point |
| `warn`, `warn <ST>`, `warn <city ST or ZIP>` | `warn KS`, `warn 78701` | Active watches, warnings and advisories: national, a state, or a place |
| `outlook <city ST or ZIP>` | `outlook Des Moines IA` | Hazardous weather outlook |
| `storm [ST or city ST]` | `storm SD` | Storm reports from the last 6 hours (the home state without an argument) |
| `rain [ST or city ST]` | `rain FL` | Rainfall reports (the home state without an argument) |
| `metar <ICAO, city ST or ZIP>` | `metar KJFK`, `metar 78701` | Raw METAR |
| `taf <ICAO, city ST or ZIP>` | `taf KJFK` | Terminal aerodrome forecast |
| `space` | `space` | Space weather |
| `sat` | `sat` | The bot's GOES receiver: lock, signal quality, packets dropped in the last minute, age of the newest EMWIN file. A bot on internet EMWIN says it has no receiver |
| `cov` | `cov` | What this bot covers: the area and how far it reaches, the NWS offices in it, how many stations it reports hourly, how often the alert list goes out |
| `more` | `more` | The next page of the last long reply |
| `help` | `help` | The command list |

Aliases: `warning`, `warnings` and `wanr` for `warn`; `storms` for `storm`;
`swx` and `solar` for `space`; `satellite`, `goes` and `signal` for `sat`;
`coverage` and `covers` for `cov`. The receiver and coverage words count
only on their own, so `satellite beach fl` and `cove tx` are still places. `<command> more` (or `next`) also means `more`. Both 3-letter
(IATA/FAA) and 4-letter (ICAO) station codes work.

### Long replies

A reply is rendered in full and cut into numbered pages at the message
budget, on item boundaries; page 1 ends `(1/3) more`. `more` from the same
person sends the next page on whichever transport it arrives, so a reply
started on the channel continues by DM. A paging session lasts 15 minutes.

### How replies go out

- **A DM** is answered by DM.
- **A command on the channel** depends on `MCW_REPLY_MODE`:
  - `dm` (default): a DM when the bot can DM the sender (its node has them
    stored, or they have DMed or adverted before). Otherwise a sender at
    most `MCW_CHANNEL_REPLY_MAX_HOPS` hops away (default 2) gets one reply
    on the channel, cut to one message ending `… DM me for all`, at most
    one per sender per 10 minutes and 12 per hour in total, and the bot
    adverts if it has not in the last hour. A sender farther away gets
    nothing.
  - `dm_only`: a DM or nothing.
  - `channel`: every reply floods on the channel, paged like a DM. For testing.
- A DM reply leaves about 2 s after the request and waits for the phone's
  ACK: attempts 0 and 1 on the stored route, then a route reset and
  attempt 2 by flood (0 and 1 by flood without a route), all with one
  timestamp. Replies to one person go one at a time, in order.
- A phone that hears no ACK sends its DM again. The same text within 2
  minutes, or the same timestamp and text within 30 minutes, is the same
  request: it gets nothing if the reply was acknowledged or is still going
  out, and the same reply again if it was never acknowledged. `more` with a
  new timestamp is a new request once the page before it was acknowledged,
  and sends the first page not acknowledged.
- A DM the node refuses to send is not retried on the channel; the bot
  forgets that sender's DM path, so their next channel command is handled
  like a stranger's.
- Messages from nodes whose name starts with `MCW_PEER_BOT_PREFIX` are
  ignored. When a peer bot's advert carries a position, a channel command
  that names a place is answered only by the bot nearest to that place.

Limits: one reply per sender every 5 seconds (2 seconds for `more`), at most
40 per sender and 400 in total per hour; a `>` request sent by DM counts
too. Anything over a limit gets no reply. A resend of a DM request is never
held to the 5 seconds and costs nothing unless something is sent for it; a
`>` request resent by DM is answered again only 12 seconds after the last
answer went out.

### Admin commands

DM only, from a public key that starts with `MCW_ADMIN_KEY`:

| Command | Description |
|---------|-------------|
| `admin` | Show admin help |
| `contacts` | List all contacts on the node |
| `remove <name>` | Remove a specific contact |
| `clear-contacts` | Remove ALL contacts from the node |
| `advert` | Send a flood advert + refresh contacts |
| `refresh` | Reload contacts from the node |
| `broadcast` | Run a scheduler tick now (the jobs that are due) |
| `warnings-broadcast` | Run the `warnings` job now |
| `test-data-ch` | Send a `test ping` text on the data channel |

## Architecture

```
meshcore_weather/
├── main.py                # Entry point: store, radio, portal; channel/DM routing, text commands, paging, limits, admin commands
├── config.py              # Settings from MCW_ environment variables and .env (pydantic-settings)
├── nlp.py                 # Text command parser: command words, aliases, bare-word sat
├── cli.py                 # meshcore-weather-cli
├── traffic.py             # What the text bot heard and said: live feed, counters, redacted public view
├── activity.py            # Broadcast log (data-channel events) for the portal
├── sdr_monitor.py         # Reads the goestools dashboard: receiver log lines and the sat reply
│
├── core/                  # One implementation per product, shared by text and binary
│   ├── services.py        # Observation, forecast, warnings, outlook, storm reports, rain, METAR/TAF for a place
│   ├── render_text.py     # One-message text renderings
│   ├── overview.py        # National and state overviews
│   ├── pages.py           # Numbered pages for long replies
│   ├── space_weather.py   # SWPC products into the space reply
│   └── vtec_names.py      # Names for VTEC phenomenon.significance pairs
│
├── emwin/
│   ├── fetcher.py         # InternetSource (NOAA zip bundles) and SDRSource (goesproc directory)
│   └── retention.py       # How long each product type stays in the store
│
├── parser/
│   ├── weather.py         # WeatherStore: products by EMWIN identifier, lookups
│   └── pfm.py             # PFM column-position parser + daily downsampler
│
├── protocol/
│   ├── v5.py              # MeshWX v5 codec (stdlib only; the reference encoder and decoder)
│   ├── v5_builders.py     # Store data -> v5 messages (one implementation for jobs and requests)
│   ├── broadcaster.py     # AppResponder: answers `>` requests, owns the Scheduler
│   ├── warnings.py        # pyIEM-backed warning extraction with storm tags
│   ├── vtec_events.py     # VTEC event lifecycle across product segments
│   ├── coverage.py        # Operator coverage (home radius, states, WFOs -> zone set)
│   └── meshwx.py, encoders.py, fec.py   # v3/v4 code, still imported for METAR parsing and shared tables
│
├── schedule/
│   ├── models.py          # BroadcastJob, BroadcastConfig; the four schedulable products
│   ├── store.py           # Atomic JSON persistence, default jobs, v4 migration
│   ├── executor.py        # Job -> v5 messages; warning state and life-safety repeats
│   └── scheduler.py       # Tick loop and the one transmit path: spacing, seq stamping, state saved across restarts
│
├── meshcore/
│   ├── radio.py           # MeshCore companion: port discovery, channels, DMs, adverts, GRP_DATA, contacts
│   ├── delivery.py        # Echo and ACK tracking, the single resend, CoreScope lookups
│   ├── health.py          # Radio health verdict (tx_suspect, rx_silent, idle, tx_off)
│   └── profile.py         # Node profile and its adoption onto a replacement radio
│
├── mqtt/
│   └── publisher.py       # Raw RX packets to an MQTT broker for CoreScope
│
├── portal/                # FastAPI admin portal (one page, hash routing, no build step)
│   ├── server.py          # App factory, state-change header check, uvicorn lifecycle
│   ├── sse.py             # SSE helper with heartbeats (logs, traffic, broadcast log)
│   ├── logbuf.py          # Log ring buffer behind System > Logs
│   ├── routes/
│   │   ├── pages.py       # GET / (the page)
│   │   ├── api.py         # Product browser, broadcast log, channels, schedule CRUD
│   │   └── admin.py       # Overview, radio, satellite, console, traffic, public bundle, logs, settings, audit
│   ├── templates/app.html
│   └── static/            # portal.js + portal.css, nothing vendored
│
├── client_data/           # Preload bundle for apps (package data; spec section 9)
│   ├── protocol.json, index.json, wfos.json
│   ├── stations.json, pfm_points.json, places.json, zips.json
│   ├── zones.json, zones.geojson, counties.json, counties.geojson
│   └── regions.json, state_index.json, weather_dict.json   # v3/v4, not used by v5
│
└── geodata/               # Offline resolver for places, stations and zones (__init__.py)
    └── zones.json, places.json, stations.json, state_index.json
```

```
deploy/      systemd units, udev rule, goestools dashboard and cleanup for the Pi
scripts/     pi_update.sh, audit.py, v5_vectors.py, build_client_data.py, build_places.py, start.sh
tests/       pytest suite
corescope/   CoreScope packet analyzer and observer brokers (separate Docker stack)
docs/        below
```

## Docs

- [`docs/MeshWX_v5_Spec.md`](docs/MeshWX_v5_Spec.md) — the protocol and the app developer's guide (wire, requests, bundle, rendering), revision 5. The current contract.
- [`docs/meshwx_v5_vectors.json`](docs/meshwx_v5_vectors.json) — test vectors every client must pass, generated by `scripts/v5_vectors.py`
- [`docs/Radio_Swap.md`](docs/Radio_Swap.md) — replacing the radio (same or different board): the node profile, adoption, the udev rule, the Health card
- [`docs/Delivery_Confirmation_Design.md`](docs/Delivery_Confirmation_Design.md) — echo tracking and the single resend: the design and the firmware facts it rests on
- [`docs/USB_Radio_Restart.md`](docs/USB_Radio_Restart.md) — what happens on the Pi when the USB radio is unplugged, dies or reboots, and what to check
- [`docs/Future_EInk_Dashboard.md`](docs/Future_EInk_Dashboard.md) — parked idea for a standalone e-ink display, written against the v4 message codes

Dated reviews, kept as records of what was found and changed at the time.
Where they disagree with the spec or the code, the spec and the code are
right.

- [`docs/MeshWX_Airtime_Review.md`](docs/MeshWX_Airtime_Review.md) — 2026-09-10: the airtime review that led to v5
- [`docs/Satellite_Feed_Findings.md`](docs/Satellite_Feed_Findings.md) — 2026-09-13: what the GOES-19 EMWIN feed on the Pi delivers
- [`docs/System_Review.md`](docs/System_Review.md) — 2026-09-13: whether the pieces between the feed and the wire agree
- [`docs/Nationwide_Design_Review.md`](docs/Nationwide_Design_Review.md) — 2026-09-14: one request channel for every bot, attacked
- [`docs/Security_Notes.md`](docs/Security_Notes.md) — 2026-09-14: what an attacker can reach before going live, and what is left
- [`docs/Accuracy_Audit_2026-09-14.md`](docs/Accuracy_Audit_2026-09-14.md) — 2026-09-14: answers checked against independent sources, the fixes, and `scripts/audit.py`
- [`docs/Admin_Portal_Review_2026-09-14.md`](docs/Admin_Portal_Review_2026-09-14.md) — 2026-09-14: the portal revamp record

## Safety

- **Channel isolation**: the radio driver never transmits on channel 0 (public) and sends only on the bot's configured text and data channel slots.
- **Receive-only switch**: `MCW_TX_ENABLED=false` turns every advert, reply, datagram and resend into a logged no-op.
- **Text reply limits**: 5 seconds per sender, 40 per sender and 400 in total per hour; a sender the bot cannot DM gets at most one channel reply per 10 minutes, 12 per hour overall, and only within the hop limit.
- **App request limits**: 5 seconds per sender and 60 answer packets per hour, checked before an answer is built.
- **Resend limits**: one resend per packet by default, 30 per hour, none while no repeater has been heard for 10 minutes.
- **Input sanitization**: control characters are stripped, messages cut to 200 characters and places to 50.
- **Admin authentication**: admin commands only by DM, matched on the sender's public key, not their name.
- **Identity**: a radio that is not the node in the saved profile sends no adverts until it is adopted or a new profile is started.
- **Portal**: no login, so keep it on the LAN; state-changing requests need the `X-Requested-With: meshcore-portal` header. The public page gets a redacted bundle.

## Roadmap

Shipped:

- [x] Internet-based EMWIN data fetching with disk cache
- [x] EMWIN from the GOES-19 dish via goestools (`MCW_EMWIN_SOURCE=sdr`)
- [x] pyIEM canonical NWS product parsing (VTEC, UGC, polygons) and a VTEC event lifecycle tracker
- [x] PFM forecast source, sent as whole days
- [x] MeshWX v5 revision 5: GRP_DATA on `#meshwx`, warning/cancel/digest/observations/forecast/text/not available/coverage, `>` request grammar, per-station observation ages and warning issue times
- [x] App requests answered on the channel so one request serves every listener
- [x] Discovery by advert (`WX-<city>` chat node)
- [x] Echo tracking and one byte-identical resend; DM ACKs; optional CoreScope check
- [x] Absolute Unix-minute expiry timestamps (no client-side countdown drift)
- [x] Per-job broadcast schedule, edited in the portal
- [x] Preload bundle (`client_data/`) with the index tables, PFM points, zone and county polygons, places, stations
- [x] Text commands for people with numbered pages and `more`
- [x] DM-first replies: reply modes, hop-limited channel replies for strangers, admin commands
- [x] Contact housekeeping
- [x] Radio swap: node profile, adoption, udev rule, health verdict
- [x] Receiver status: `sat` and `>sat`
- [x] Coverage statement: `cov`, `>cov`, and one packet every 3 h, so an app never guesses the bot's area
- [x] Admin portal: overview, text bot feed and console, broadcasts, radio, satellite, logs and settings
- [x] Public dashboard card with a redacted live feed
- [x] Accuracy audit against api.weather.gov, IEM, aviationweather.gov and SWPC
- [x] MQTT packet publishing for CoreScope
- [x] Pi deployment: systemd unit, git-based updates with `scripts/pi_update.sh`
- [x] Docker container (radio over a TCP bridge)

Planned:

- [ ] iOS client against v5
- [ ] A permanent `GRP_DATA` data type requested upstream (spec section 2.1)
- [ ] H-VTEC hydrologic metadata (flood severity, river ID, stage forecast)
- [ ] 3-hourly hour-by-hour PFM forecast format
- [ ] Standalone e-ink weather display hardware product (see `docs/Future_EInk_Dashboard.md`)

## License

Apache License 2.0. Copyright 2026 Rafael Pesquera. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Authors

Created and maintained by Rafael Pesquera ([@digitaino](https://github.com/digitaino)). Contributions from the Austin mesh community are welcome; see the open protocol docs under `docs/`.

## Related

- **[DigitainoMesh](https://github.com/digitaino/DigitainoMesh)** — a native MeshCore client for iOS and iPadOS.
- **[meshwx-client](https://github.com/digitaino/meshwx-client)** — desktop and web client for the earlier MeshWX v4 protocol; it does not decode v5.
