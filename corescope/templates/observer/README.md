# AUS Meshcore observer — quick start

You've been given this folder by an AUS Meshcore operator. It contains
everything you need to start contributing your radio's RX traffic to
their dashboard.

## Requirements

- Docker (Linux, macOS, or Windows with WSL2 — anything that runs Docker
  Compose).
- A Meshcore radio plugged in over USB.

## Steps

1. (If your radio is *not* on `/dev/ttyUSB0`) edit two files:
   - `docker-compose.yml` → change the `devices:` line to your device path.
   - `config.toml` → change the `ports = [...]` line to match.

   Common paths: `/dev/ttyUSB0`, `/dev/ttyACM0`. On macOS look for
   `/dev/cu.usbserial-*` from `ls /dev/cu.usb*`.

2. Start it:

   ```bash
   docker compose up -d
   ```

   The first run builds Cisien's `meshcoretomqtt` from source (~2 min).
   Subsequent restarts are instant.

3. Watch the logs to confirm it's running:

   ```bash
   docker compose logs -f
   ```

   You should see lines about connecting to `mqtt.digitaino.com:443` and
   then packets being published.

4. Within ~30 seconds your observer should appear on the dashboard at
   <https://meshcore.digitaino.com> (or wherever the operator points
   you). It'll show your `iata` code and the radio's public key.

## Stopping

```bash
docker compose down
```

## Troubleshooting

- **`Connection Refused: not authorised`** — the username or password is
  wrong, or the operator revoked your credential. Ping them.
- **Container exits immediately** — usually means the radio device path
  is wrong. Check `ls /dev/tty*` (Linux) or `ls /dev/cu.usb*` (macOS).
- **Builds successfully but no packets** — the radio may not be hearing
  any traffic (check antenna, power, region). The bridge will only
  publish what the radio receives.

## Privacy

Only the radio's RX is sent — every packet it overhears, in raw form.
Encrypted channel messages stay encrypted unless the operator has the
key. Nothing about *you* is sent.
