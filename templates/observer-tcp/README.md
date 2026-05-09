# AUS Meshcore observer — meshcore-proxy variant

Use this bundle if your Pi (or whatever host) is already running
[`meshcore-proxy`](https://github.com/rgregg/meshcore-proxy) against
your radio. We attach as a second, **read-only** TCP client to the
same proxy — nothing about your existing setup changes, no serial
conflicts, no new radio.

## Requirements

- Docker.
- meshcore-proxy already running and reachable. If it's on this same
  host (the default), nothing else to do — `network_mode: host` lets
  the observer reach `localhost:5000`.

## Steps

1. (Only if meshcore-proxy runs on a *different* machine.) Edit
   `docker-compose.yml` and change `PROXY_HOST=127.0.0.1` to the IP or
   hostname where meshcore-proxy is listening.

2. Start it:

   ```bash
   docker compose up -d
   ```

   First run builds a small Python image (~30 sec). Subsequent restarts
   are instant.

3. Watch the logs:

   ```bash
   docker compose logs -f
   ```

   You should see lines about connecting to the proxy, reading the
   radio's pubkey, and "MQTT connected".

4. Within ~30 seconds you'll appear on the AUS Meshcore dashboard under
   your IATA code.

## Stopping

```bash
docker compose down
```

Your meshcore-proxy and Meshcore companion app are untouched.

## Troubleshooting

- **`Could not read public_key from radio via proxy — aborting`**:
  meshcore-proxy is reachable but the radio isn't answering. Likely
  the proxy is up but the USB radio is disconnected or stuck. Restart
  meshcore-proxy.
- **`Connection refused`**: meshcore-proxy isn't listening on
  `PROXY_HOST:PROXY_PORT`. Verify with
  `nc -zv 127.0.0.1 5000` from the Pi.
- **`Connection Refused: not authorised` (MQTT)**: the operator's
  credential for you is wrong or revoked. Ping them.

## Privacy

Only the radio's RX is published — every packet it overhears, in raw
form. Encrypted channel messages stay encrypted unless the operator
has the key.
