# AUS Meshcore observer — meshcore-proxy variant

Use this bundle if your Pi (or whatever host) is already running
[`meshcore-proxy`](https://github.com/rgregg/meshcore-proxy) against
your radio. We attach as a second, **read-only** TCP client to the
same proxy — nothing about your existing setup changes, no serial
conflicts, no new radio.

You have two ways to run it; pick whichever fits your host. Both
produce identical behavior.

## Option 1: Docker (simplest if you already have Docker)

```bash
docker compose up -d
docker compose logs -f       # confirm it's running
```

If meshcore-proxy is on a different machine, edit `docker-compose.yml`
and change `PROXY_HOST=127.0.0.1` to its IP/hostname.

## Option 2: Native Python (no Docker)

This is the lightest path on a Pi. Requires Python 3.10+.

```bash
./run-native.sh              # one-liner: makes a venv, installs, runs
```

(Your `.env` is already filled in by the operator's bundle generator; no
manual edits needed unless meshcore-proxy is on a different host.)

`run-native.sh` creates a `.venv/` next to itself on first run (~30s),
then runs `observer.py` in the foreground. Stop with Ctrl-C.

For permanent install on a Pi, create a systemd unit (replace
`/home/pi/aus-observer` with the actual path):

```ini
# /etc/systemd/system/aus-observer.service
[Unit]
Description=AUS Meshcore observer
After=network-online.target meshcore-proxy.service
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/aus-observer
ExecStart=/home/pi/aus-observer/run-native.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now aus-observer
sudo journalctl -u aus-observer -f
```

## Confirming it works

Whichever option you pick, you should see log lines about:
1. Connecting to the proxy at `127.0.0.1:5000`
2. Reading the radio's pubkey
3. "MQTT connected"

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

## Privacy

Only the radio's RX is published — every packet it overhears, in raw
form. Encrypted channel messages stay encrypted unless the operator
has the key.
