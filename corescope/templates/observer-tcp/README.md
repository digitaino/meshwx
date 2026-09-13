# AUS Meshcore observer — native variant

Two ways this bundle can connect to your radio (set in `.env`):

- **`OBSERVER_MODE=tcp`** (default) — connect to an existing
  [`meshcore-proxy`](https://github.com/rgregg/meshcore-proxy)
  instance on your host. Use this if you also need the Companion app
  (or other client) to share the radio.
- **`OBSERVER_MODE=serial`** — connect directly to the radio over USB.
  Simplest setup, but the observer is the **sole consumer** of the
  radio (stop meshcore-proxy / Companion app first).

Two install paths (independent of which mode you pick):

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

## Switching to direct serial mode

If your radio isn't shared with anything else (no Companion app, no
other meshcore client on this host), running direct over USB is the
simplest setup — no meshcore-proxy involved.

1. Stop and disable meshcore-proxy if you have one:

   ```bash
   sudo systemctl stop meshcore-proxy
   sudo systemctl disable meshcore-proxy
   ```

2. Find a stable device path for your radio (don't use `/dev/ttyUSB0`
   directly — that name can change across reboots):

   ```bash
   ls -l /dev/serial/by-id/
   ```

   Pick the entry that points to your meshcore radio.

3. Edit `.env` and set:

   ```
   OBSERVER_MODE=serial
   SERIAL_PORT=/dev/serial/by-id/usb-...     # whatever you found above
   SERIAL_BAUD=115200
   ```

4. Re-run `./run-native.sh` to test, then `sudo ./install.sh` for the
   systemd install. The install script automatically adds the service
   user to the `dialout` group so it can read the serial device.

## Troubleshooting

- **`Radio did not respond to APPSTART within Ns ...`** (tcp mode):
  observer reached the proxy but the proxy never got a reply from
  the radio. Tail proxy logs (`sudo journalctl -u meshcore-proxy -f`)
  while running observer to see what's happening on the proxy side.
  If you can't get the proxy to forward, switch to serial mode (above).
- **`Radio did not respond to APPSTART within Ns ...`** (serial mode):
  the radio is at the wrong path, isn't powered, or another process
  is holding the serial port. Check `lsof "$SERIAL_PORT"` and confirm
  meshcore-proxy is fully stopped.
- **`Connection refused`** (tcp mode): meshcore-proxy isn't listening
  on `PROXY_HOST:PROXY_PORT`. Verify with `nc -zv 127.0.0.1 5000`.
- **`Connection Refused: not authorised` (MQTT)**: the operator's
  credential for you is wrong or revoked. Ping them.
- **Service keeps restarting**: inspect journald with
  `sudo journalctl -u aus-observer -n 100`. Most often this is the
  proxy or radio not yet up at boot — it'll settle within a couple
  of `RestartSec` cycles.

## Privacy

Only the radio's RX is published — every packet it overhears, in raw
form. Encrypted channel messages stay encrypted unless the operator
has the key.
