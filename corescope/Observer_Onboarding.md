# Observer Onboarding — AUS Meshcore CoreScope

A Meshcore radio elsewhere can contribute its RX traffic to the AUS Meshcore
dashboard. Three onboarding paths, pick whichever fits the observer's setup.

## For operators (you)

```bash
corescope/scripts/add-observer.sh <username> [iata]              # USB-radio (host computer + Cisien's bridge in Docker)
corescope/scripts/add-observer.sh --proxy <username> [iata]      # alongside an existing meshcore-proxy
corescope/scripts/add-observer.sh --firmware <username> [iata]   # radio is the entire observer (no host)
```

| Variant | When to use it | Hand-off |
|---|---|---|
| (default) | Observer has a USB Meshcore radio + a Linux host (Pi etc.) and is happy with Docker. | Tarball with docker-compose. |
| `--proxy` | Observer already runs [`rgregg/meshcore-proxy`](https://github.com/rgregg/meshcore-proxy) on their host (Meshcore companion app, Home Assistant). We attach a second read-only TCP client. | Tarball with `install.sh` / `run-native.sh`. |
| `--firmware` | Observer flashed the [agessaman MQTT-bridge firmware fork](https://github.com/agessaman/MeshCore/tree/mqtt-bridge-implementation-flex) onto a Heltec V3/V4, Station G2, or LilyGo. The radio publishes directly over WiFi — no host needed. | Paste-ready `set` block + link to [`Observer_Setup_Firmware.md`](./Observer_Setup_Firmware.md). |

For the bundle variants, the script:

1. Generates a bcrypt-hashed credential and writes it to
   `corescope/mosquitto/passwords` (gitignored).
2. SIGHUPs the broker so the new credential is live without dropping
   existing connections.
3. Builds a bundle at `corescope/out/observer-bundles/<username>/`
   with the right files filled in for the chosen variant.
4. Tars it as `corescope/out/observer-bundles/<username>.tar.gz`
   for hand-off.

Hand the tarball to the observer (any channel — DM, email, drop on a
shared drive). Don't commit the tarball anywhere — `corescope/out/`
is gitignored for that reason.

For the **firmware** variant, no bundle is generated; the script prints
the credential plus a paste-ready block of `set` commands for the
radio's serial console. Send that block plus the
[`Observer_Setup_Firmware.md`](./Observer_Setup_Firmware.md) link to
the observer.

To rotate a password, just run `add-observer.sh` again with the same
username — `mosquitto_passwd` updates in place. Send the new bundle.

To revoke:

```bash
corescope/scripts/remove-observer.sh <username>
```

To see who's online and how many packets they've published:

```bash
corescope/scripts/list-observers.sh
```

## For observers (whoever you handed the tarball to)

For the **bundle variants**, the bundle's own `README.md` walks them
through it. The short version is:

```bash
tar xzf <username>.tar.gz
cd <username>
docker compose up -d
docker compose logs -f       # confirm it's connected
```

Within ~30 seconds they'll show up on the dashboard under their IATA
code. The first `docker compose up` builds Cisien's `meshcoretomqtt`
from GitHub (~2 min); subsequent restarts are instant.

For the **firmware variant**, see
[`Observer_Setup_Firmware.md`](./Observer_Setup_Firmware.md). The
observer connects to their radio over USB at 115200 baud, pastes the
`set` block, runs `save` and `reboot`, and the radio takes over from
there.

## What gets published

Every RF packet the observer's radio receives is JSON-published to:

```
meshcore/<IATA>/<radio-pubkey>/packets
```

Body: `origin`, `origin_id`, `timestamp`, `raw` (hex), `SNR`, `RSSI`,
`route` (`F` flood / `D` direct), `packet_type`, `path`, `hash`. Encrypted
channel messages stay encrypted unless the operator has the channel
key. Nothing about the observer themselves is sent — only what the radio
overhears.

## How the auth + transport work

```
observer ─wss://mqtt.digitaino.com/─► CF tunnel ─► localhost:9001 ─► mosquitto
                                                                       │
                                                                  username/password
                                                                       │
                                                                       ▼
                                                                 corescope ingest
```

- Cloudflare Tunnel terminates TLS publicly and forwards to Mosquitto's
  WebSocket listener. Configured on the host's `~/.cloudflared/config.yml`
  (or via the dashboard's Public Hostname for that tunnel).
- Mosquitto enforces username/password on every connection on both
  listeners (TCP `1883` for local Docker services, WS `9001` for remote).
- WebSocket upgrade is auto-detected by cloudflared. No extra flags.

## Troubleshooting (operator)

- New observer never shows up in `corescope/scripts/list-observers.sh`:
  - Check broker logs: `docker logs mosquitto --tail 30`. Auth failures
    show as `Connection from … denied: not authorised`.
  - Check the tunnel: `curl -I https://mqtt.digitaino.com/` should
    return a 426 Upgrade Required from cloudflared (that's fine — it
    means HTTP got there, the next hop wants WebSocket).
- Observer reports the bundle's docker compose `build:` step failing
  (USB-radio variant only): upstream Cisien repo may have moved a
  branch. Edit `corescope/templates/observer/docker-compose.yml` to
  pin a commit, then regenerate bundles.
