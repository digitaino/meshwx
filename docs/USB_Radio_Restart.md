# USB Radio Restart Guide

When the USB radio is disconnected and reconnected (or the Mac restarts), the
meshcore-weather container will keep running but silently fail to send messages.
The symptom is `Binary send failed on data ch 6: {'reason': 'no_event_received'}`
in the container logs.

## Quick fix

From the `meshcore-weather` project directory:

```bash
docker compose up --build --force-recreate -d
```

The `--force-recreate` flag is important — without it Docker may see no image
changes and skip the restart. The socat bridge (TCP:4403 <-> serial) stays
running and reconnects automatically when the USB device reappears. Only the
container needs a recreate to re-establish the connection through socat.

## How to confirm it's working

```bash
docker logs meshcore-weather --tail 20
```

You should see:

```
Connecting to Meshcore radio via TCP host.docker.internal:4403
Listening on channel 3 (#digitaino-wx-bot)
Data channel 6 (#aus-meshwx-v4)
Discovery channel 5 (#meshwx-discover)
Sent advertisement (flood)
Meshcore radio connected.
```

If you see `Sent ch6: XXB` lines appearing every few minutes, data is flowing.

## How to tell it's broken

```bash
docker logs meshcore-weather --tail 50 | grep -i "warn\|error\|fail"
```

If you see repeated lines like:

```
WARNING: Binary send failed on data ch 6: {'reason': 'no_event_received'}
```

...the serial connection is dead and the container needs a restart.

## If socat also died

The socat bridge is what forwards TCP port 4403 to the USB serial device. It
normally survives USB reconnects, but if it's not running:

```bash
# Check if socat is alive
lsof -i :4403

# If nothing shows up, restart it:
socat TCP-LISTEN:4403,reuseaddr,fork OPEN:/dev/cu.usbserial-0001,raw,echo=0,ispeed=115200,ospeed=115200 &
```

Then restart the container as above.

## USB device not showing up at all

If `/dev/cu.usbserial-0001` doesn't exist after reconnecting the cable:

```bash
ls /dev/cu.usb*
```

If nothing appears, the radio isn't being recognized by macOS. Try a different
USB port or cable.

## CoreScope MQTT bridge

The stack also runs `corescope` and `mosquitto` containers alongside
`meshcore-weather`. CoreScope is a meshcore packet analyzer with a web
dashboard; Mosquitto is the auth-required broker between them. The weather
bot publishes raw RX packets to Mosquitto so CoreScope can decode and
visualize them — passive piggyback, no extra radio writes.

All of CoreScope's config and observer-management tooling lives under
`corescope/`. See `corescope/README.md` for the layout and
`corescope/Observer_Onboarding.md` for the full operator flow.

- **Dashboard**: <http://localhost:8082>
- **Broker**: `mosquitto:1883` inside the Docker network, also exposed on the
  host as `localhost:1883` for debugging with `mosquitto_sub`.
- **Topic**: `meshcore/AUS/<radio-pubkey>/packets` — JSON in the
  Cisien/CoreScope format.

### Toggling MQTT publishing

Set `MCW_MQTT_ENABLED` in `.env`:

- `false` — bot runs as before, no MQTT traffic, CoreScope sees nothing.
- `true` — bot publishes every received RF packet.

After flipping the value, run `docker compose up --build --force-recreate -d`.

### Verifying it's flowing

```bash
# stats from CoreScope
curl -s http://localhost:8082/api/stats | python3 -m json.tool

# raw packets on the broker (you'll need a credential — see corescope/README.md)
mosquitto_sub -h localhost -u corescope -P "$(grep ^MCW_MQTT_PASSWORD .env | cut -d= -f2)" -t 'meshcore/#' -v
```

If the bot ever stops working after enabling MQTT, set
`MCW_MQTT_ENABLED=false` and recreate — MQTT is fail-soft and disabled by
default, so this is a safe rollback.
