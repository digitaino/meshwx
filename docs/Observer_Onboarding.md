# Observer Onboarding — AUS Meshcore CoreScope

This is for someone running a Meshcore radio elsewhere who wants to contribute
their RX packets to the AUS Meshcore dashboard.

## What you need

- A Meshcore radio (Heltec, T-Beam, etc.) connected by USB to a Linux box, Pi,
  or Mac that's left running.
- Python 3.10+ (or Docker — your call).
- The broker URL and a username/password from the dashboard operator. The
  broker speaks MQTT-over-WebSockets through Cloudflare, so you connect
  outbound on port 443 — no port-forwarding on your side.

## Quick start with `Cisien/meshcoretomqtt`

This is the canonical observer software. It supports MQTT-over-WebSockets
natively, which is what we use.

1. Install:

   ```bash
   git clone https://github.com/Cisien/meshcoretomqtt.git
   cd meshcoretomqtt
   pip install -r requirements.txt
   ```

2. Create `config.toml`:

   ```toml
   # Required: broker connection
   [[broker]]
   name = "aus-meshcore"
   enabled = true
   server = "mqtt.digitaino.com"   # ← from operator
   port = 443
   transport = "websockets"
   username = "observer-yourname"        # ← from operator
   password = "<secret>"                 # ← from operator
   iata = "AUS"                          # 3-letter regional code

   [broker.tls]
   enabled = true                        # Cloudflare terminates TLS

   # Required: which radio(s) to read
   [[serial]]
   port = "/dev/ttyUSB0"                 # adjust to your device
   baud = 115200
   ```

3. Run:

   ```bash
   python -m meshcoretomqtt --config config.toml
   ```

4. Verify on the dashboard. Within ~30 seconds, your observer should show up
   under Map → Observers. Your `origin_id` (the radio's public key prefix) and
   chosen IATA code identify you.

## What gets sent

Every RF packet your radio receives is JSON-published to topic:

```
meshcore/<IATA>/<your-radio-pubkey>/packets
```

Body fields: `origin`, `origin_id`, `timestamp`, `raw` (hex), `SNR`, `RSSI`,
`route` (`F` flood / `D` direct), `packet_type`, `path`, `hash`. The dashboard
operator's CoreScope decodes and aggregates them.

Nothing about *you* is sent — only what the radio overhears on the mesh.
Encrypted channel messages stay encrypted unless the operator has the key.

## Asking the operator for credentials

DM the operator with:

- The IATA / regional code you want to use (3 letters; pick something close to
  your geography — `AUS`, `DAL`, `HOU`, etc.)
- A short identifier for your observer (e.g. `nathan-eastside`).

They'll generate a username/password specifically for you and reply with them.
Keep them out of your repo — `.env` or a non-committed `config.toml` is fine.

## Troubleshooting

- **`Connection Refused: not authorised`** — your username/password is wrong,
  or your account was revoked. Ask for fresh creds.
- **`Connection refused` (TCP) or TLS handshake errors** — make sure
  `transport = "websockets"`, `port = 443`, and `[broker.tls] enabled = true`.
  Plain MQTT TCP won't work through Cloudflare's HTTP edge.
- **Connected but no packets flowing** — check that your radio is actually
  hearing traffic (`mosquitto_sub` locally first, before the WebSocket
  transport, to isolate). Or pipe the script's stderr to look for
  `published` lines.

## Operator notes (you may ignore if you're an observer)

To add a new observer credential to the broker, on the operator's host:

```bash
docker run --rm -v "$(pwd)/mosquitto/passwords:/passwords" \
  eclipse-mosquitto:2 mosquitto_passwd -b /passwords <username> '<password>'
docker compose restart mosquitto
```

To revoke:

```bash
docker run --rm -v "$(pwd)/mosquitto/passwords:/passwords" \
  eclipse-mosquitto:2 mosquitto_passwd -D /passwords <username>
docker compose restart mosquitto
```
