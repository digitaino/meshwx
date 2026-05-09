# CoreScope (observer side)

Everything specific to the CoreScope packet analyzer + the regional
observer network it ingests from. The weather bot itself lives under
`meshcore_weather/`; the two stacks share `docker-compose.yml` and
`.env` at the repo root but are otherwise independent.

## Layout

```
corescope/
├── config.json                   # live CoreScope config (gitignored — has apiKey)
├── config.example.json           # sanitized template
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
├── Observer_Onboarding.md        # operator-facing flow doc (all variants)
└── Observer_Setup_Firmware.md    # observer-facing setup for firmware variant
```

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

The weather bot publishes its own radio's RX packets to the same
broker (under the `weatherbot` MQTT user). It's just another packet
source from CoreScope's perspective. The wiring lives in
`meshcore_weather/mqtt/publisher.py` and is gated by
`MCW_MQTT_ENABLED` in the root `.env`.
