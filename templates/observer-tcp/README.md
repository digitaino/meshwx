# AUS Meshcore observer — meshcore-proxy variant

Use this bundle if your Pi (or whatever host) is already running
[`meshcore-proxy`](https://github.com/rgregg/meshcore-proxy) against
your radio. We attach as a second, **read-only** TCP client to the
same proxy — nothing about your existing setup changes, no serial
conflicts, no new radio.

Three install paths, in increasing order of "set and forget":

1. **`./run-native.sh`** — quick test in the foreground; Ctrl-C to stop.
2. **`docker compose up -d`** — Docker, restarts on reboot.
3. **`sudo ./install.sh`** — production install on a Pi with systemd.
   Survives reboots, restarts on failure, runs as a hardened
   non-root system user. **Recommended for permanent use.**

Pick one. They're not exclusive but you only need one.

## Option 1: Quick test (foreground)

```bash
./run-native.sh              # Ctrl-C to stop
```

`run-native.sh` creates a `.venv/` next to itself on first run (~30s),
then runs `observer.py`. Use this to confirm everything works before
committing to a permanent install.

## Option 2: Docker

```bash
docker compose up -d
docker compose logs -f
```

If meshcore-proxy is on a different machine, edit `docker-compose.yml`
and change `PROXY_HOST=127.0.0.1` to its IP/hostname.

## Option 3: Production install (recommended for Pi)

```bash
sudo ./install.sh
```

This is the same install pattern Cisien's `meshcoretomqtt` uses
(`/opt/<service>/`, dedicated system user, hardened systemd unit). It:

- Creates an unprivileged system user `aus-observer` (no shell, no home).
- Installs the code and a Python venv to `/opt/aus-observer/`.
- Installs `aus-observer.service` to systemd, hardened with
  `ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`,
  `PrivateTmp=true`, `LockPersonality=true`, `RestrictRealtime=true`,
  `RestrictSUIDSGID=true`.
- Sets `Restart=always`, `RestartSec=10` so transient failures
  self-heal without manual intervention.
- Waits for `time-sync.target` and `network-online.target` so the
  service starts in the right order at boot.
- Enables and starts the service immediately.

Logs go to journald:

```bash
sudo journalctl -u aus-observer -f      # follow live
sudo systemctl status aus-observer      # current state
```

To rotate the password later, get a fresh `.env` from the operator
and replace `/opt/aus-observer/.env`, then:

```bash
sudo systemctl restart aus-observer
```

To uninstall (removes service, files, and the system user):

```bash
sudo ./uninstall.sh
```

## Confirming it works

Whichever option you picked, the logs should show:
1. Connecting to the proxy at `127.0.0.1:5000`
2. Reading the radio's pubkey
3. `MQTT connected (rc=Success)`

Within ~30 seconds you'll appear on the AUS Meshcore dashboard under
your IATA code.

## Troubleshooting

- **`Could not read public_key from radio via proxy — aborting`**:
  meshcore-proxy is reachable but the radio isn't answering. Likely
  the proxy is up but the USB radio is disconnected or stuck.
  Restart meshcore-proxy.
- **`Connection refused`**: meshcore-proxy isn't listening on
  `PROXY_HOST:PROXY_PORT`. Verify with `nc -zv 127.0.0.1 5000`.
- **`Connection Refused: not authorised` (MQTT)**: the operator's
  credential for you is wrong or revoked. Ping them.
- **Service keeps restarting** (option 3): inspect journald,
  `sudo journalctl -u aus-observer -n 100`. Most often this is
  meshcore-proxy not yet up at boot — it'll settle within a couple
  of `RestartSec` cycles, but you can add an explicit
  `After=meshcore-proxy.service` to the unit if your meshcore-proxy
  also runs under systemd.

## Privacy

Only the radio's RX is published — every packet it overhears, in raw
form. Encrypted channel messages stay encrypted unless the operator
has the key.
