# Becoming a CoreScope observer

**There is nothing to request and no credentials to issue.** Your node proves
who it is by signing a short-lived token with its own MeshCore Ed25519 key.
Point any observer client at the broker below and it will start publishing.

| | |
|---|---|
| **Broker** | `wss://obs.digitaino.com` |
| **Port** | `443` |
| **Transport** | WebSocket over TLS (TLS terminated at Cloudflare) |
| **Auth** | MeshCore auth token — signed by your device key |
| **Token audience** | `obs.digitaino.com` *(must match exactly)* |
| **Username / password** | leave blank |
| **IATA region** | `AUS` |
| **Topics** | `meshcore/AUS/{YOUR_PUBLIC_KEY}/packets` and `/status` |

Your data shows up at <https://scope.digitaino.com/#/observers> within a few
minutes of the first packet.

Two things follow from the token model, and they're the point of it:

- You can only publish under **your own** public key. The broker rejects
  anything else, so no one can forge your observer's traffic — or you theirs.
- Nobody has to hand you a password over Signal, and there's nothing to
  rotate, leak, or revoke later.

---

## MeshCore observer firmware (Heltec V3/V4, T-LoRa, Station G2, Tracker)

Flash an observer build (e.g. <https://observer.gessaman.com>), then paste this
into the serial console at 115200 baud — or use <https://config.meshcore.io>.

The firmware holds **three independent broker slots**. Pick one that's free:
if you already publish to MeshTexas or MeshMapper on `mqtt1`, use `mqtt2` or
`mqtt3` here and keep both. Replace `N` below with your chosen slot.

```
set mqttN.preset custom
set mqttN.server obs.digitaino.com
set mqttN.port 443
set mqttN.audience obs.digitaino.com
set mqtt.iata AUS
reboot
```

Setting `.audience` is what puts the slot in token mode — it's why there is no
username or password line. Verify after the reboot:

```
get wifi.status        → connected, with an IP
get mqttN.audience     → obs.digitaino.com
get public.key         → this is how you'll find yourself on the map
```

## MeshCore Home Assistant integration

In the integration's MQTT settings:

- **Server** `obs.digitaino.com`, **Port** `443`
- **Transport** `websockets`, **TLS** on, **Verify certificates** on
- **Username / Password** — leave both blank
- **Use MeshCore Auth Token** — checked
- **Token Audience** `obs.digitaino.com`
- **Broker IATA Code** `AUS`

## MeshMonitor (Analyzer Observer)

Enable the Analyzer Observer fieldset on your MeshCore source, set the region
to `AUS`, then click **Custom…** to add a broker row:

- **Broker URL** `wss://obs.digitaino.com`
- **Broker authentication** `Signed token`
- **Token audience** `obs.digitaino.com`

Then supply the signing key on the source's MeshCore → Configuration page,
either via **Fetch from device** or by pasting the 128-character hex private
key. MeshMonitor requires a Companion device, not a Repeater.

## meshcoretomqtt / meshcore-packet-capture / PyMC / openHop

All of these take the same five values — host `obs.digitaino.com`, port `443`,
websockets + TLS, auth mode "MeshCore auth token", audience
`obs.digitaino.com`, IATA `AUS` — in whatever config file the project uses.
Follow the upstream install docs and add a custom broker with those settings;
several of them support publishing to multiple brokers at once.

---

## Already publishing to us with a username and password?

Keep doing exactly that — the old broker at `mqtt.digitaino.com` is unchanged
and isn't going anywhere. When you next have your radio in front of you,
switching to the settings above is a strict upgrade, and you can drop the
password. Nothing breaks if you never get around to it.

## Troubleshooting

**Connects, then immediately drops.** Audience mismatch is the usual cause: it
has to be exactly `obs.digitaino.com`, with no `wss://` and no port.

**Connects but nothing appears on the map.** Check your IATA is `AUS` — the
broker only accepts topics under a valid region code, and packets published
under a different one won't reach this analyzer.

**Drops every minute or two.** Cloudflare closes WebSockets that sit idle for
about 100 seconds. Keep the client's MQTT keepalive well under that; 45–60
seconds works.
