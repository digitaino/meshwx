# AUS Meshcore observer — meshcore-proxy variant

Use this bundle if your Pi (or whatever host) is already running
[`meshcore-proxy`](https://github.com/rgregg/meshcore-proxy) against
your radio. We attach as a second, **read-only** TCP client to the
same proxy — nothing about your existing setup changes, no serial
conflicts, no new radio.

Two install paths:

1. **`./run-native.sh`** — quick foreground test, Ctrl-C to stop. Use
   this first to confirm everything works before installing.
2. **`sudo ./install.sh`** — production install. systemd-managed,
   restarts on failure, survives reboots, runs as a hardened
   non-root system user. **The recommended path for permanent use.**

## Option 1: Foreground test

```bash
./run-native.sh
```

`run-native.sh` creates a `.venv/` next to itself on first run (~30s),
then runs `observer.py` in the foreground. Stop with Ctrl-C.

You should see log lines about:
1. Connecting to the proxy at `127.0.0.1:5000`
2. Reading the radio's pubkey
3. `MQTT connected (rc=Success)`

If those appear, you're good — move to option 2 for permanent install.

## Option 2: Production install

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

## Configuration changes after install

If `meshcore-proxy` is on a different machine, edit `/opt/aus-observer/.env`
and change `PROXY_HOST=127.0.0.1` to its IP/hostname, then:

```bash
sudo systemctl restart aus-observer
```

To rotate the password later, replace `/opt/aus-observer/.env` with the
new contents from the operator and restart the same way.

To uninstall (removes service, files, and the system user):

```bash
sudo ./uninstall.sh
```

## Troubleshooting

- **`Could not read public_key from radio via proxy — aborting`**:
  meshcore-proxy is reachable but the radio isn't answering. Likely
  the proxy is up but the USB radio is disconnected or stuck.
  Restart meshcore-proxy.
- **`Connection refused`**: meshcore-proxy isn't listening on
  `PROXY_HOST:PROXY_PORT`. Verify with `nc -zv 127.0.0.1 5000`.
- **`Connection Refused: not authorised` (MQTT)**: the operator's
  credential for you is wrong or revoked. Ping them.
- **Service keeps restarting**: inspect journald,
  `sudo journalctl -u aus-observer -n 100`. Most often this is
  meshcore-proxy not yet up at boot — it'll settle within a couple
  of `RestartSec` cycles.

## Privacy

Only the radio's RX is published — every packet it overhears, in raw
form. Encrypted channel messages stay encrypted unless the operator
has the key.
