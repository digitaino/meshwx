# USB Radio Restart Guide

What happens when the USB radio is unplugged, dies or reboots, and what to
check. Production is the receiver Raspberry Pi: the bot runs as
`meshcore-weather.service` under systemd, with the radio on USB.

> 2026-09-15: rewritten for the Pi. The earlier version described a Docker
> container on a Mac that kept running but silently failed after a replug,
> a socat bridge, and two launchd agents that recreated the container. The
> bot now notices a lost link and reconnects by itself.

## What happens on its own

The bot notices a lost link in any of three ways
(`meshcore_weather/meshcore/radio.py`):

- the serial layer reports the port closed (at once);
- the device node disappears from `/dev` (checked every 15 s);
- the node answers nothing to three commands in a row (one every 15 s).

It logs `Radio link lost: <reason>` and `Reconnecting to the radio after a
lost link`, then connects again in the background. It tries the configured
port (`MCW_SERIAL_PORT`, `/dev/meshcore` on the Pi), then `/dev/meshcore`,
then every USB serial port it can see (`/dev/serial/by-id/*`,
`/dev/ttyACM*`, `/dev/ttyUSB*`). ESP32 boards reset when the port opens, so
it waits for the boot and asks up to three times.

If nothing answers, it logs `Radio not available after reconnect (...);
retrying every 60s` and tries again every minute (`RADIO_RETRY_SECONDS` in
`main.py`). When a radio answers: `Radio connected after retry`.

While the radio is away the bot keeps running: the EMWIN store, the portal
and the CLI stay up. Scheduled broadcasts and app requests stop until the
radio is back; the broadcaster then starts again and the sequence number
continues from `data/warning_state.json`. The same retry loop covers a bot
started with no radio plugged in.

A radio that answers with a different key is a replacement board: see
`docs/Radio_Swap.md`.

## When the bot process exits

`deploy/meshcore-weather.service` has `Restart=always` and `RestartSec=5`.
Any exit (a crash, System › Restart in the portal, `scripts/pi_update.sh`,
the 700 MB memory limit) brings the bot back five seconds later with the
current `.env`.

A stop or a restart is a clean one: on SIGTERM the bot cancels its tasks,
logs `Weather bot stopped` and exits 0, so `systemctl status` after a deploy
shows `inactive (dead)`, not `failed`. The new process is listening on the
radio within seconds; its EMWIN products load behind it (README, *First-run
verification*).

## How to confirm it's working

```bash
systemctl status meshcore-weather
journalctl -u meshcore-weather -f
ls -l /dev/meshcore
```

A healthy start looks like this (the Pi, 2026-09-15):

```
Connecting to Meshcore radio on /dev/meshcore @ 115200 baud
Node firmware v1.17.1-d929643 (14-Aug-2026), 350 contact slots
Listening on channel 1 (#meshwx)
Data channel 1 (#meshwx, shared with text)
Meshcore radio connected. Node: WX-AUS
Weather bot is running. Listening on channel 1 (#meshwx) + DMs
```

Two lines of the same start say how long each half took: `Meshcore radio
connected … s after start` (how long the node was off the air) and `Backlog
loaded: N products in … s` (when the answers stopped being "starting up").

`Sent data on ch 1: ... bytes (type 0xFF10)` lines mean broadcasts are going
out, and `Delivery channel_data: echoed via ...` means a repeater carried
one. In the portal, the status strip shows `radio down` while the link is
gone, and Radio › Health › **Test transmit** checks that the radio is
getting out.

## How to tell it's broken

```bash
journalctl -u meshcore-weather --since "1 hour ago" | grep -E "Radio link lost|Reconnecting|Radio not available|connected after retry"
```

Repeated `Radio not available` lines mean no port answers: see below.

## Doing it by hand

- **Portal**: Radio › **Reconnect now** drops the link and connects again,
  exactly as after a lost link.
- **Whole bot**: `sudo systemctl restart meshcore-weather`.
- **Radio**: unplug and replug it; the bot notices within 15 s.

## USB device not showing up at all

```bash
ls -l /dev/meshcore /dev/serial/by-id/ /dev/ttyACM* /dev/ttyUSB*
```

On the Pi the Heltec V4 is `/dev/ttyACM0`, and `/dev/meshcore` points at it.

- `ttyACM0` or `ttyUSB0` exists but `/dev/meshcore` does not: the udev rule
  is missing or does not know the board's USB chip. The bot scans every
  port anyway; install the rule for a stable name:

  ```bash
  sudo cp deploy/99-meshcore-radio.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger
  ```

- No tty at all: try another cable (charge-only cables carry no data) or
  another port. A board in bootloader mode shows up as a different device;
  press reset once.
- `no companion response on ...`: the port opens but nothing answers. The
  firmware must be MeshCore **Companion Radio (USB)**; BLE-only builds and
  repeater firmware never answer on serial.

## Docker on a Mac

`docker-compose.yml` still runs the bot in a container that reaches the
radio over TCP (`MCW_SERIAL_PORT=tcp://host.docker.internal:4403`), with a
bridge on the host forwarding that port to the USB serial device. The bridge
is not in this repository. The bot handles a dead TCP link the same way (a
closed connection, or three unanswered commands) and retries every 60 s; the
port scan and the `/dev` check apply only to a serial port.

## CoreScope MQTT bridge

When `MCW_MQTT_ENABLED=true` (default `false`) the bot publishes every
packet its radio receives to the Mosquitto broker at `MCW_MQTT_HOST`
(default `mosquitto`, the service name in `docker-compose.yml`), on
`meshcore/<IATA>/<radio-pubkey>/packets`, as JSON in the CoreScope format.
The observer name CoreScope shows comes from `MCW_MQTT_ORIGIN` (default
`meshcore-weather`). MQTT is fail-soft: a broker that is down never stops
the bot.

The setting is read when the radio connects, so restart the bot after
changing it. On 2026-09-15 the Pi does not publish: it runs no broker and no
Docker.

CoreScope, Mosquitto and the signed-token observer broker are defined in
`docker-compose.yml`. See `corescope/README.md` for the layout and
`corescope/Observer_Onboarding.md` for the operator flow.
