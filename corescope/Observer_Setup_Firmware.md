# Observer Setup — Firmware-Flashed Radio (agessaman MQTT-bridge fork)

This guide is for observers running the
[agessaman/MeshCore `mqtt-bridge-implementation-flex`](https://github.com/agessaman/MeshCore/tree/mqtt-bridge-implementation-flex)
firmware fork on a Heltec V3 / V4, Station G2, or LilyGo variant. Once
configured, the radio publishes every RX packet directly to the AUS
Meshcore broker over WiFi. **No host computer needed at the deployment
site.**

If you don't already have this firmware flashed, follow the upstream
[`MQTT_IMPLEMENTATION.md`](https://github.com/agessaman/MeshCore/blob/mqtt-bridge-implementation-flex/MQTT_IMPLEMENTATION.md)
to build and flash it first. **Flash the Repeater variant, not
Companion** — Companion firmware doesn't relay/observe traffic the
way we need. Come back here for configuration.

> The operator gives you two things: the setup-guide URL (this page)
> and a paste-ready block of `set` commands containing your unique
> credentials. If you don't have the paste block yet, ping them.

## What you'll need

- A flashed radio (Repeater variant, see above).
- A USB cable to the radio.
- A computer running a recent **Chrome, Edge, or Brave** browser (Web
  Serial API; Firefox/Safari aren't supported yet).
- A WiFi network reachable from where the radio will be deployed —
  ESP32 WiFi is **2.4 GHz only**.

## 1. Open the web config tool

The friendliest path is the official Meshcore web config tool, which
includes a free-form serial console — no terminal app, no remembering
how to exit `screen`:

→ **<https://config.meshcore.io>**

1. Plug the radio into your computer via USB.
2. Click **Connect** in the page.
3. In the browser's USB-port chooser, pick the radio (it'll show up as
   "Heltec…", "USB Serial Device", or similar).
4. Find the **CLI Console** section (also labelled "Send manual CLI
   commands to the device" in the UI).

That's where you'll paste the block. Skip to step 2.

> If you'd rather use a terminal app instead of the browser, see the
> [terminal fallback](#terminal-fallback) section at the bottom.

## 2. Paste the configuration block

The operator's `add-observer.sh --firmware <your-name>` produces a
block that looks like this — your block has real credentials:

```
set wifi.ssid YOUR_WIFI_SSID
set wifi.pwd  YOUR_WIFI_PASSWORD
set mqtt3.preset custom
set mqtt3.server mqtt.digitaino.com
set mqtt3.port 443
set mqtt3.username <your-issued-username>
set mqtt3.password <your-issued-password>
set mqtt.iata AUS
save
reboot
```

**Before pasting, replace `YOUR_WIFI_SSID` and `YOUR_WIFI_PASSWORD`**
with your actual WiFi credentials. The MQTT credentials you leave as
the operator gave them.

Paste the entire block at the prompt. Each `set` line gets
acknowledged in turn. `save` commits the configuration to flash.
`reboot` restarts the radio.

If your WiFi password contains a literal space, the firmware accepts
it on the rest of the line — `set wifi.pwd my super secret` works.

## 3. Verify it's running

After the reboot finishes (~5 seconds), the web tool's CLI console
should reconnect (or click **Connect** again). You'll see lines
indicating the radio is joining WiFi and then connecting to MQTT. If
you missed them, query state:

```
get wifi.status
```

Expected: `connected` plus an IP address.

```
get mqtt3.preset
get mqtt3.server
get mqtt3.username
get mqtt.iata
```

Expected: `custom`, `mqtt.digitaino.com`, your username, and `AUS`.

You can also watch the live log stream — the firmware prints `MQTT
connected` (or equivalent) and then a brief log line per RX packet.

## 4. Operator confirms on the dashboard

Tell the operator your radio's pubkey (the firmware logs it on boot,
or you can query `get device.pubkey`). They'll run:

```
corescope/scripts/list-observers.sh
```

…and within ~30 seconds your row appears with `IATA = AUS`,
`1H_PKTS` ticking up, and `AGE` in seconds. That's confirmation the
end-to-end path is working.

## 5. Permanent deployment

Once you've confirmed packets are flowing, unplug the radio from your
computer and plug it into a USB power supply at the deployment site
(within range of the WiFi you configured). The radio rejoins WiFi
and resumes publishing automatically — its config persisted to flash
in step 2.

There's no service to manage on any computer. The radio is the
entire observer.

## Troubleshooting

- **`get wifi.status` says "disconnected" indefinitely.**
  - Typo in SSID or password — re-run `set wifi.ssid …` /
    `set wifi.pwd …`, then `save` and `reboot`.
  - Network is 5 GHz only. ESP32 WiFi is 2.4 GHz; you'll need a
    2.4 GHz network or a dual-band SSID that exposes 2.4 GHz.

- **WiFi connects but MQTT can't.**
  - Re-check `get mqtt3.username` and `get mqtt3.password`. Typos
    here will cause an auth-fail / reconnect loop.
  - Operator may have rotated or revoked your credentials. Ping
    them; they'll regenerate the block with fresh creds.

- **MQTT connects, but no packets show on the dashboard.**
  - The radio's RF parameters (frequency, spreading factor,
    bandwidth) need to match the local mesh, otherwise the radio
    can't overhear traffic. This is a separate config done via the
    same serial console; consult the upstream firmware docs.

- **Heltec V4-specific:** confirm the firmware was built with the V4
  variant. V3 firmware on V4 hardware may flash without errors but
  won't fully work (different SoC pinout).

## Privacy

Only the radio's RX is published — every packet it overhears, in raw
form, plus the radio's own pubkey and reported SNR/RSSI. Encrypted
channel messages stay encrypted unless the operator has the channel
key. Nothing about you (your IP, WiFi, etc.) is sent.

## Caveats

- This firmware fork is a branch of the upstream MeshCore project.
  Flashing it replaces the official MeshCore firmware on that radio.
  Flash the **Repeater** variant — Companion firmware doesn't relay
  the traffic an observer needs to see.
- WiFi must be available at the deployment site.
- Memory constraints in the firmware: up to 6 MQTT slots with PSRAM,
  only 2 concurrent TLS/WSS slots without PSRAM. We use slot 3
  (`mqtt3.*`) for AUS Meshcore so observers can keep slots 1-2 for
  their preferred preset (LetsMesh, etc.).

## Terminal fallback

If you'd rather skip the browser and use a serial-terminal app
directly, that works too. Same `set` block, same baud rate.

**macOS:**

```bash
ls /dev/cu.usb*
# Pick the matching path — Heltec V4 shows up as /dev/cu.usbmodem*
screen /dev/cu.usbmodem<id> 115200
```

**Linux:**

```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
# V3 (CP2102) is usually /dev/ttyUSB0
# V4 (native USB-CDC) is usually /dev/ttyACM0
screen /dev/ttyACM0 115200
```

**Windows:** Open Device Manager → Ports (COM & LPT) to find the COM
number. Open PuTTY, set Connection type = Serial, Serial line =
`COM<n>`, Speed = `115200`. Open.

To exit `screen`: press `Ctrl-A`, then `K`, then `y`.
