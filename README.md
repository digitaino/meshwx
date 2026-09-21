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
- **Two clients.** A native iOS app ([DigitainoMesh](https://github.com/digitaino/DigitainoMesh)) and a web client in this repository (`web/`), which runs in any Chromium browser and talks to a MeshCore radio over Web Bluetooth or Web Serial. Same wire format, same tables, same words: see [The web client](#the-web-client).
- **Text replies for people.** Anyone on `#meshwx` can send `wx austin tx`, `warn TX` or `sat`, on the channel or by DM, and get a text reply, by DM where the bot can reach them. Long replies are paged with `more`. This is how people without the app use the bot.
- **One request grammar for apps and people.** An app sends `>f 102` and gets a binary answer on the channel for every listener; a person sends `forecast austin tx` and gets text. An app's `>` arrives as a flooded Request datagram (v5 type 9), by DM, or as channel text; the answer is the same either way.
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
| Radar tiles from the dish's EMWIN radar pictures (`radar`, `>radar`, spec revision 11) | shipped, request only |
| Admin portal and public dashboard | shipped |
| Accuracy audit (`scripts/audit.py`) | shipped |
| MQTT packet publishing for CoreScope | shipped, off by default |
| iOS client | in beta, revision 11 |
| Web client (`web/`), Chromium over Bluetooth or USB | shipped, revision 11 |
| A downloadable copy of the web client (`web/tools/package.mjs`) | shipped |
| Standalone e-ink dashboard | idea parked in `docs/Future_EInk_Dashboard.md` |

## The web client

`web/` is the whole weather tool as a web page: alerts, conditions, forecasts,
the area map and radar, from a `WX-` bot over a MeshCore radio on **Web
Bluetooth** or **Web Serial**. It is a port of the iOS tool layer for layer and
follows the same documents, so the two say the same things in the same words.
No build step, no dependencies, no internet: the tables, the map outlines and
the eleven languages are all in the folder, and after the first visit the
browser keeps it and it works offline.

What a person needs:

| | |
|---|---|
| A computer, and a browser on it | Chrome, Edge, Brave or Vivaldi on macOS, Windows, Linux or Chrome OS. Safari and Firefox have no Web Bluetooth or Web Serial. A phone is the iOS app's job |
| A radio | A MeshCore companion radio, firmware 1.15 or newer, over Bluetooth or USB. A radio talks to one companion at a time, so it has to be disconnected from the phone's MeshCore app first |
| A bot in reach | Any `WX-` node on `#meshwx`. Without one there is still `?link=demo`, a recorded morning |
| A way to open the folder | The page has to come from `https://` or `http://localhost`, which is what a browser calls a secure context and what it wants before it hands out Bluetooth. `file://` will not do |

From this checkout, for development:

```bash
cd web
node tools/dev-server.mjs          # http://localhost:8137
node --test test/                  # the suite
```

The dev server serves `/data/` straight out of `meshcore_weather/client_data/`,
so a table the bot regenerates is on the page at the next reload, and it
proxies the debug bridge. Details, and the porting rules, in
[`web/README.md`](web/README.md).

### Giving it to somebody else

They do not need this repository, Python, or anything the bot needs:

```bash
cd web
npm run package                    # dist/meshwx-web/ and dist/meshwx-web.zip
```

`web/tools/package.mjs` copies the client and the parts of `client_data/` it
reads into one folder that is a plain static site, and adds a README and a
small `serve.mjs`. Nothing is bundled or minified: what ships is the code in
this repository, file for file. The download is 5.7 MB, 20.5 MB unfolded,
most of it the zone and county outlines — `--no-outlines` leaves those out for
1.9 MB, and the maps then draw without the shapes.

Whoever receives it unzips it and double-clicks `start-macos.command` or
`start-windows.bat`, which runs `serve.mjs` and opens the browser. Node is the
one thing they have to install first. By hand it is `node serve.mjs`, or
`python3 -m http.server 8137`, and then `http://localhost:8137`.

The built zip is attached to the
[latest release](https://github.com/digitaino/meshwx/releases/latest), which is
the link to give people: [`web/README.md`](web/README.md) is written for them
and says what they need in five lines.

None of this touches the internet, which is the point. The client holds no
absolute URL, every fetch it makes is same-origin and relative, and `serve.mjs`
listens on 127.0.0.1: a machine that has never been online runs the whole
thing. Getting the file there wants no network either — a USB stick, an SD
card, AirDrop, a share on the LAN, or a Pi on the mesh handing the zip out over
plain http, because downloading a file needs no secure context. Only the page
that talks to the radio does, and that page is at `localhost`.

A phone is not part of this: it cannot serve itself `localhost`, so it wants an
address rather than a folder, and the iOS app is the phone answer. Chrome on
Android has Web Bluetooth and would run these files, but only from an https
address, which off-grid means a certificate the phone already trusts on the
local network. Only worth the trouble if Android ever has to be served.

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
| 8 | Coverage: centre, radius, NWS offices and the zone runs this bot carries | 14 B + 1 per office + 4 per run | every 3 h, on request |
| 9 | Request: an app's `>` request, the one message that travels app -> bot | 14 B + the text (a `>d` is 16 B) | sent by the app, flooded on `#meshwx` |

Requests start with `>` and reach the bot three ways: as a **Request
datagram** (type 9, the normal path since spec revision 6), as a DM, or as
text on `#meshwx`. Whichever road a request took, the answer is the same:
v5 messages flooded on `#meshwx`, to everyone, never as a DM.

A Request datagram carries six bytes of the sender's public key, the
sender's own Unix seconds and the `>` text (at most 40 bytes). It names the
bot it is asking in the header's `bot` field — `0xFFFF` asks every bot on
the channel — and a bot ignores one that names another bot. It replaced the
DM because a flood needs no route: a DM rides one stored route and is lost
silently once that route goes stale, which is what happened to seven
requests in six minutes on 16 September while the bot was answering
everyone else. The DM form still works, for an app whose firmware cannot
send channel datagrams (`CMD_SEND_CHANNEL_DATA`, 0x3E).

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
| `>radar`, `>radar 30.270,-97.740`, `>radar austin tx z1` | One tile of the newest radar picture, 32 x 32 cells in four precipitation levels, always one packet. `z0` to `z3` widens it from 2 to 16 degrees. Needs the dish: the pictures are EMWIN GIFs |
| `>sat` | The bot's GOES receiver, one line of Text |
| `>cov` | What this bot covers: centre, radius, NWS offices, zone runs. One packet, also broadcast every 3 h |

Limits: one request per sender every 5 s and 60 answer packets per hour
across all senders. A `>` sent as a DM also counts against the text-command
limits; a Request datagram and a `>` line on the channel do not. One phone
is one sender however it asks: the six key bytes in a datagram are the same
prefix its DMs carry. A resend (the same sender, text and timestamp) is
answered again only once the previous answer finished going out at least
12 s earlier. A throttled request gets no reply.

The full byte layouts, the preload bundle, rendering guidance and these
rules in detail: **[`docs/MeshWX_v5_Spec.md`](docs/MeshWX_v5_Spec.md)**
(revision 7) and [`docs/meshwx_v5_vectors.json`](docs/meshwx_v5_vectors.json).
Reference codec: [`meshcore_weather/protocol/v5.py`](meshcore_weather/protocol/v5.py).

## For client developers (iOS, web, embedded)

Start and finish with **`docs/MeshWX_v5_Spec.md`**, revision 11. Decode the
test vectors, ship the `client_data/` bundle, follow the request rules. If you
built against an earlier revision, its sections 16 to 16F are what changed and
in which order; no field has ever moved, and an unknown type is ignored, so an
old client keeps working and simply learns less.

Two implementations to read against it, both complete and both carrying the
vectors as tests: the iOS app in
[DigitainoMesh](https://github.com/digitaino/DigitainoMesh) and the web client
in [`web/`](web/README.md), which is the smaller of the two to read and needs
nothing but a browser to run.

Revision 6 adds one message, and it is the only one an app sends: the
Request datagram of section 7B. Send `>` requests that way — same channel,
same `data_type`, type nibble 9 — and keep the DM form only for a radio
whose firmware has no `CMD_SEND_CHANNEL_DATA` (0x3E). Ask once, and if
nothing has come back after 10 s send the same bytes once more, same
timestamp; never a third time. The `request_digest` vector is those sixteen
bytes.

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

### The debug bridge: an app client with no radio

A development client (the iOS app in a simulator, a script, a web page on
your laptop) can be a live client of a **real** bot without a radio: it
reads the datagrams the bot transmits and asks for one through the same
path a DM takes. Requests are not simulated — `AppResponder.handle_request`
runs, the per-sender spacing and the hourly budget apply, and the answer
goes on the air to everybody as usual, which is why the client sees it.

Off unless **both** `MCW_PORTAL_ENABLED=true` and `MCW_BRIDGE_TOKEN` are
set. Every call carries the token in `X-Bridge-Token` (or
`Authorization: Bearer …`); a bridge that is off answers 404. Bind the
portal to localhost and reach it through an SSH tunnel — nothing here is
meant to face a network.

| Call | What it does |
|---|---|
| `GET /api/bridge/stream?since=<cursor>` | SSE. One JSON frame per datagram transmitted — `cursor`, `ts`, `ts_iso`, `data_type`, `hex`, `length`, `resend`, `attempt` — after the stream's `{"hello": true}`. The last 200 datagrams are kept, so `since` resumes where you left off |
| `GET /api/bridge/datagrams?since=&limit=` | The same ring as one JSON page: `datagrams`, `cursor`, `latest`, `gap` (`true` when what you missed has already left the ring) |
| `POST /api/bridge/request` | `{"text": ">o KAUS", "client": "sim"}`. Always 200 with `outcome`: `sent` (with `packets`, `bytes`), `rate_limited` (with `retry_after`) or `budget_spent`. The client id is the sender, so two clients are two senders. Like every portal POST it also needs `X-Requested-With: meshcore-portal` |
| `GET /api/bridge/info` | The bot's name, public key, bot id, channel and data type |

The feed is hooked at the one choke point every datagram passes through
(`MeshcoreRadio.send_channel_data`), so its bytes are exactly what went on
the air, sequence number already stamped, and an echo resend shows up as its
own frame with `resend: true`.

On the bot (the Pi's `.env`):

```bash
MCW_PORTAL_ENABLED=true
MCW_PORTAL_HOST=127.0.0.1      # the tunnel's far end; never 0.0.0.0 for this
MCW_BRIDGE_TOKEN=<a long random string>
```

From your laptop — the remote port is whatever `MCW_PORTAL_PORT` says (the
receiver Pi runs the portal on 8081, because 8080 there is the goestools
dashboard):

```bash
ssh -N -L 8080:127.0.0.1:8080 digitaino@mesh-wx.digitaino.com   # Pi: …:127.0.0.1:8081
curl -H "X-Bridge-Token: $TOKEN" http://127.0.0.1:8080/api/bridge/info
curl -N -H "X-Bridge-Token: $TOKEN" http://127.0.0.1:8080/api/bridge/stream
curl -H "X-Bridge-Token: $TOKEN" -H "X-Requested-With: meshcore-portal" \
     -H "Content-Type: application/json" -d '{"text":">cov","client":"sim"}' \
     http://127.0.0.1:8080/api/bridge/request
```

The iOS app takes the same two values from its launch environment
(`MESHWX_BRIDGE_URL`, `MESHWX_BRIDGE_TOKEN`) and uses them instead of the
radio; see `RemoteBotWeatherTransport` in the app repo.

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

On start the bot loads its geodata, connects to the radio (or retries every
minute until one answers), starts the scheduler once a data channel is up,
and starts the portal if enabled. The EMWIN products come in **behind** the
radio: the node is listening within seconds of a restart, and the backlog
already on disk (`internet`: NOAA's 1-hour bundle, then the 2-minute bundle
every `MCW_EMWIN_POLL_INTERVAL` seconds; `sdr`: the goesproc directory) is
read and parsed in a worker thread while it listens.

Until that backlog is in — a few seconds for the internet source, up to a
couple of minutes for a Pi with tens of thousands of files:

- a DM that needs products is answered "Starting up: my weather products
  are still loading. Ask again in a minute." without spending any of that
  sender's hourly reply budget (a command on the channel gets the same
  sentence, and is rate-limited as a channel reply always is);
- a `>` request — by datagram, by DM or as channel text — is answered Not
  available, reason 0 (no data yet), off the hourly packet budget, so the
  app's own retry still gets a real answer;
- `help`, `cov` and `sat` are answered normally: none of them reads a product;
- the scheduler broadcasts nothing, so no digest, observation or coverage
  message is ever built from an empty store.

### First-run verification

On the first start you should see log lines like these (slot numbers vary):

```
[INFO] meshcore_weather.geodata: Location data loaded: 4029 zones, 34937 places, 2237 stations
[INFO] meshcore_weather.meshcore.radio: Listening on channel 1 (#meshwx)
[INFO] meshcore_weather.meshcore.radio: Data channel 1 (#meshwx, shared with text)
[INFO] meshcore_weather.main: Meshcore radio connected 10.4 s after start
[INFO] meshcore_weather.schedule.store: Bootstrap schedule: 4 default jobs
[INFO] meshcore_weather.schedule.scheduler: Broadcast scheduler started: 4 jobs, tick every 30s
[INFO] meshcore_weather.schedule.scheduler: Broadcasts held until the product backlog is loaded
[INFO] meshcore_weather.portal.server: Portal running at http://0.0.0.0:8080
[INFO] meshcore_weather.main: Weather bot is running. Listening on channel 1 (#meshwx) + DMs
[INFO] meshcore_weather.main: Backlog loaded: 14508 products in 39.3 s
```

`Meshcore radio connected … after start` is how long the node was off the
air, and `Backlog loaded` is when the answers became real ones. A stop
(`systemctl stop`, Ctrl-C, or Restart from the portal) cancels every task,
logs `Weather bot stopped` and exits 0.

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

The node is deaf only between the stop and the radio connect of the new
process — seconds, not the time the product backlog takes (see *First-run
verification*). `journalctl -u meshcore-weather` shows the two numbers that
matter, `Meshcore radio connected … after start` and `Backlog loaded`.

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
| `MCW_RADAR_DIR` | empty | Where the EMWIN radar GIFs are (`YYYY-MM-DD/*-RAD*.GIF`). Empty means `MCW_SDR_EMWIN_DIR` when the source is `sdr`, and no radar otherwise: the internet bundle carries no images |
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
| `MCW_BRIDGE_TOKEN` | *(empty)* | Set it to turn on the debug bridge (needs the portal too): a development client reads the transmitted datagrams and posts `>` requests over HTTP. Empty = the bridge answers 404. See "The debug bridge" |

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
| `radar [city ST or ZIP]` | `radar Dallas TX` | The newest radar picture in words: its time and age, what is over the place, the nearest precipitation and the nearest heavy core (the home city without an argument). A bot without a dish says it has none |
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
too. A `>` request that arrives as a Request datagram or as channel text does
not: it meets only the app limits (5 s per sender, 60 answer packets an
hour). Anything over a limit gets no reply. A resend of a request is never
held to the 5 seconds and costs nothing unless something is sent for it; a
`>` request sent again, by datagram or by DM, is answered again only 12
seconds after the last answer went out.

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
├── main.py                # Entry point: startup order (radio first, products behind it), shutdown; channel/DM routing, text commands, paging, limits, admin commands
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
│   ├── v5.py              # MeshWX v5 codec (stdlib only; the reference encoder and decoder, Request included)
│   ├── v5_builders.py     # Store data -> v5 messages (one implementation for jobs and requests)
│   ├── broadcaster.py     # AppResponder: answers `>` requests, owns the Scheduler
│   ├── warnings.py        # pyIEM-backed warning extraction with storm tags
│   ├── vtec_events.py     # VTEC event lifecycle across product segments
│   ├── coverage.py        # Operator coverage (home radius, states, WFOs -> zone set)
│   └── meshwx.py, encoders.py, fec.py   # v3/v4 code, still imported for METAR parsing and shared tables
│
├── radar/
│   ├── picture.py         # One EMWIN radar GIF -> precipitation levels: its own colour scale, furniture masks, the printed time
│   ├── tiles.py           # The 32 x 32 tile a phone asked for, cut from a picture; the words of the `radar` reply
│   ├── source.py          # Newest picture per product in the dish's EMWIN directory, decoded on demand
│   ├── service.py         # What `>radar` and `radar` both ask: the best fresh picture for a tile
│   └── products.json, masks/, stamp_digits.json   # Calibration, written by scripts/radar_calibrate.py
│
├── schedule/
│   ├── models.py          # BroadcastJob, BroadcastConfig; the four schedulable products
│   ├── store.py           # Atomic JSON persistence, default jobs, v4 migration
│   ├── executor.py        # Job -> v5 messages; warning state and life-safety repeats
│   └── scheduler.py       # Tick loop and the one transmit path: spacing, seq stamping, state saved across restarts
│
├── meshcore/
│   ├── radio.py           # MeshCore companion: port discovery, channels, DMs, adverts, GRP_DATA in and out, contacts
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
scripts/     pi_update.sh, audit.py, v5_vectors.py, radar_calibrate.py, build_client_data.py, build_places.py, start.sh
tests/       pytest suite
corescope/   CoreScope packet analyzer and observer brokers (separate Docker stack)
docs/        below
```

## Docs

- [`docs/MeshWX_v5_Spec.md`](docs/MeshWX_v5_Spec.md) — the protocol and the app developer's guide (wire, requests, bundle, rendering), revision 11. The current contract.
- [`docs/meshwx_v5_vectors.json`](docs/meshwx_v5_vectors.json) — test vectors every client must pass, generated by `scripts/v5_vectors.py`
- [`docs/Radio_Swap.md`](docs/Radio_Swap.md) — replacing the radio (same or different board): the node profile, adoption, the udev rule, the Health card
- [`docs/Delivery_Confirmation_Design.md`](docs/Delivery_Confirmation_Design.md) — echo tracking and the single resend: the design and the firmware facts it rests on
- [`docs/USB_Radio_Restart.md`](docs/USB_Radio_Restart.md) — what happens on the Pi when the USB radio is unplugged, dies or reboots, and what to check
- [`docs/Future_EInk_Dashboard.md`](docs/Future_EInk_Dashboard.md) — parked idea for a standalone e-ink display, written against the v4 message codes
- [`web/README.md`](web/README.md) — the web client: running it, packaging it for somebody else, the radio settings screen, the radar card, and how the folder is laid out
- [`web/docs/PORTING.md`](web/docs/PORTING.md) — the rules the port follows: Swift names kept, layer order, what a screen may import
- [`web/docs/UI_KIT.md`](web/docs/UI_KIT.md) — the client's own small UI framework, for anyone adding a screen to it

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
- [x] MeshWX v5 revision 6: the Request datagram (type 9) — an app's `>` flooded on `#meshwx` instead of DMed down a route that may have gone stale
- [x] MeshWX v5 revision 7: the data source in the flags nibble (GOES dish / internet / both), and a cut flag on Text replies trimmed at a sentence instead of mid-word
- [x] MeshWX v5 revision 8: `>o` answers with the nearest station within 40 km that has a fresh report, not silence from a station that sends nothing
- [x] MeshWX v5 revision 9: Area sweep (type 10), the national picture of what is active as runs of UGC numbers
- [x] MeshWX v5 revision 10: `>part` for a missing part, `>wmap` by state, `>f lat,lon`, and forecast points rebuilt so a place without a PFM gets the nearest one that has it
- [x] MeshWX v5 revision 11: Radar (type 11), one tile of an EMWIN mosaic in one packet, request only
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
- [x] iOS client against v5 (in beta, revision 11)
- [x] Web client for Chromium browsers over Web Bluetooth or Web Serial, and a downloadable copy of it

Planned:

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
