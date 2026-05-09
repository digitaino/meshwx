# Observer Onboarding — AUS Meshcore CoreScope

A Meshcore radio elsewhere can contribute its RX traffic to the AUS Meshcore
dashboard. The setup on the observer's side is one tarball + one command.

## For operators (you)

```bash
./scripts/add-observer.sh <username> [iata]
```

This:

1. Generates a bcrypt-hashed credential and writes it to
   `mosquitto/passwords` (gitignored).
2. SIGHUPs the broker so the new credential is live without dropping
   existing connections.
3. Builds a bundle at `out/observer-bundles/<username>/` containing
   `docker-compose.yml`, a pre-filled `config.toml`, and a `README.md`.
4. Tars it as `out/observer-bundles/<username>.tar.gz` for hand-off.

Hand the tarball to the observer (any channel — DM, email, drop on a
shared drive). Don't commit the tarball anywhere — `out/` is gitignored
for that reason.

To rotate a password, just run `add-observer.sh` again with the same
username — `mosquitto_passwd` updates in place. Send the new bundle.

To revoke:

```bash
./scripts/remove-observer.sh <username>
```

To see who's online and how many packets they've published:

```bash
./scripts/list-observers.sh
```

## For observers (whoever you handed the tarball to)

The bundle's `README.md` walks them through it. The short version is:

```bash
tar xzf <username>.tar.gz
cd <username>
docker compose up -d
docker compose logs -f       # confirm it's connected
```

Within ~30 seconds they'll show up on the dashboard under their IATA
code. The first `docker compose up` builds Cisien's `meshcoretomqtt`
from GitHub (~2 min); subsequent restarts are instant.

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

- New observer never shows up in `./scripts/list-observers.sh`:
  - Check broker logs: `docker logs mosquitto --tail 30`. Auth failures
    show as `Connection from … denied: not authorised`.
  - Check the tunnel: `curl -I https://mqtt.digitaino.com/` should
    return a 426 Upgrade Required from cloudflared (that's fine — it
    means HTTP got there, the next hop wants WebSocket).
- Observer reports the bundle's docker compose `build:` step failing:
  upstream Cisien repo may have moved a branch. Edit
  `templates/observer/docker-compose.yml` to pin a commit, regenerate
  bundles.
