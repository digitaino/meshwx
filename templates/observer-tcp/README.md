# AUS Meshcore observer — meshcore-proxy variant

Use this bundle if your Pi (or whatever host) is already running
[`meshcore-proxy`](https://github.com/rgregg/meshcore-proxy) against
your radio. We attach as a second, **read-only** TCP client to the
same proxy — nothing about your existing setup changes, no serial
conflicts, no new radio.

Four install paths. Pick whichever fits — they're not exclusive but you
only need one:

1. **`./run-native.sh`** — quick test in the foreground; Ctrl-C to stop.
   Zero install, no persistence.
2. **`./install-user.sh`** — **sudoless.** Registers a user-level systemd
   service under your own account. Survives reboots if you enable
   linger (one sudo command, optional). Best path if you'd rather not
   give an installer root.
3. **`docker compose up -d`** — Docker, restarts on reboot. Needs
   Docker installed; auto-restart handled by the Docker daemon.
4. **`sudo ./install.sh`** — system install. Dedicated `aus-observer`
   system user under `/opt/`, hardened systemd unit. Most robust but
   needs root once.

## Option 1: Quick test (foreground)

```bash
./run-native.sh              # Ctrl-C to stop
```

`run-native.sh` creates a `.venv/` next to itself on first run (~30s),
then runs `observer.py`. Use this to confirm everything works before
committing to a permanent install.

## Option 2: Sudoless user-mode systemd (recommended for Pi)

```bash
./install-user.sh
```

This registers a user-level systemd unit at
`~/.config/systemd/user/aus-observer.service` and installs the code +
venv at `~/.local/share/aus-observer/`. No root, no `/opt`, no system
user. It restarts automatically on failure and watches journald with:

```bash
journalctl --user -u aus-observer -f
systemctl --user status aus-observer
```

By default user services only run **while you're logged in**. To make
the service start at boot and survive logouts, enable linger (one
sudo, only ever once):

```bash
sudo loginctl enable-linger $USER
```

If you skip lingering, just keep your SSH session open or restart the
service manually after each reboot with
`systemctl --user start aus-observer`.

To uninstall: `./uninstall-user.sh`.

## Option 3: Docker

```bash
docker compose up -d
docker compose logs -f
```

If meshcore-proxy is on a different machine, edit `docker-compose.yml`
and change `PROXY_HOST=127.0.0.1` to its IP/hostname.

## Option 4: System install with sudo

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
