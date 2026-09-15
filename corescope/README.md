# CoreScope (observer side)

Everything specific to the CoreScope packet analyzer + the regional
observer network it ingests from. The weather bot itself lives under
`meshcore_weather/`; the two stacks share `docker-compose.yml` at the repo
root but are otherwise independent (the CoreScope services read their own
config under `corescope/`, not the root `.env`). In production the bot runs
on the receiver Pi under systemd, outside that compose file.

## Layout

```
corescope/
├── config.json                   # live CoreScope config (gitignored — has apiKey)
├── config.example.json           # sanitized template
├── broker/                       # signed-token observer broker (obs.digitaino.com)
│   ├── Dockerfile                # pinned build of michaelhart/meshcore-mqtt-broker
│   ├── .env.example              # config template
│   └── .env                      # live config (gitignored — subscriber password)
├── mosquitto/
│   ├── mosquitto.conf            # broker config (anonymous off, TCP+WS)
│   ├── passwords                 # bcrypt creds (gitignored)
│   └── passwords.example         # how to bootstrap the file
├── scripts/
│   ├── add-observer.sh           # add (or rotate) an observer credential
│   ├── remove-observer.sh        # revoke
│   ├── list-observers.sh         # show who's online (queries CoreScope API)
│   └── set-node-location.sh      # manually pin a node's lat/lon
├── templates/
│   ├── observer/                 # bundle: USB-attached radio (Cisien-based)
│   └── observer-tcp/             # bundle: meshcore-proxy variant
├── out/                          # generated bundles (gitignored)
├── Observer_Onboarding_Token.md  # observer-facing: the token broker (preferred)
├── Observer_Onboarding.md        # operator-facing flow doc (legacy username/password)
└── Observer_Setup_Firmware.md    # observer-facing setup for firmware variant
```

## Two brokers, on purpose

New observers should use the **token broker** at `obs.digitaino.com`. They
authenticate by signing a token with their node's own Ed25519 key, so there is
nothing to issue and nothing to revoke — and the broker enforces that a node
can only publish under its own public key. Send them
`Observer_Onboarding_Token.md`; you do nothing.

The **legacy broker** at `mqtt.digitaino.com` (Mosquitto, username/password,
`mosquitto/passwords`) still carries the ~15 observers onboarded before the
switch. It is deliberately untouched: several of those radios don't reliably
reconnect after a broker restart, so they get migrated one at a time, whenever
their operator next has hands on the hardware. The scripts below are for
maintaining that population — not for new observers.

CoreScope ingests both as two `mqttSources`, so the split is invisible
downstream and shows up only as two rows in the MQTT sources panel.

## Common commands

All of these can be run from the repo root or from inside `corescope/` —
the scripts find their own paths.

```bash
# add an observer (USB radio + Linux host with Docker)
corescope/scripts/add-observer.sh <username>

# add an observer (already runs meshcore-proxy on their host)
corescope/scripts/add-observer.sh --proxy <username>

# add an observer (radio runs the agessaman MQTT-bridge firmware fork
# directly — no host computer; prints a paste-ready `set` block)
corescope/scripts/add-observer.sh --firmware <username>

# revoke
corescope/scripts/remove-observer.sh <username>

# see who's online
corescope/scripts/list-observers.sh
```

For the full operator flow see `Observer_Onboarding.md`. For the
firmware-flashed observer setup steps, see
`Observer_Setup_Firmware.md`.

## Where the bot fits in

With `MCW_MQTT_ENABLED=true` in the bot's `.env` (default `false`), the
weather bot publishes its own radio's RX packets to the legacy Mosquitto
broker (`MCW_MQTT_HOST`, default `mosquitto`; `mosquitto/passwords.example`
names the user `weatherbot`). It's just another packet source from
CoreScope's perspective. The wiring lives in
`meshcore_weather/mqtt/publisher.py`. On 2026-09-15 the production bot on
the receiver Pi does not publish: there is no broker on the Pi.
