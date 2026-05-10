# Observer Setup — Firmware-Flashed Radio (agessaman MQTT-bridge fork)

This guide is for observers running the
[agessaman/MeshCore `mqtt-bridge-implementation-flex`](https://github.com/agessaman/MeshCore/tree/mqtt-bridge-implementation-flex)
firmware fork on a Heltec V3 / V4, Station G2, or LilyGo variant. Once
configured, the radio publishes every RX packet directly to the AUS
Meshcore broker over WiFi. **No host computer needed at the deployment
site.**

If you don't already have this firmware flashed, follow the upstream
[`MQTT_IMPLEMENTATION.md`](https://github.com/agessaman/MeshCore/blob/mqtt-bridge-implementation-flex/MQTT_IMPLEMENTATION.md)
to build and flash it first. The fork's build targets are all named
`*_repeater_observer_mqtt` — **the radio runs as a repeater (with
MQTT publishing added), not as a Companion device.** If your radio is
currently paired to a phone as a Companion, flashing this firmware
will replace that role; you'd need a second radio if you want to keep
your Companion. Come back here for configuration.

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
block like this — your block has real credentials substituted by the
script:

```
set prv.key <64-hex-Ed25519-seed>
set radio 910.525,62.5,7,5
set tx 22
set name aus-obs-<your-name>
set mqtt.iata AUS
set wifi.ssid YOUR_WIFI_SSID
set wifi.pwd  YOUR_WIFI_PASSWORD
set mqtt3.preset custom
set mqtt3.server mqtt.digitaino.com
set mqtt3.port 443
set mqtt3.username <your-issued-username>
set mqtt3.password <your-issued-password>
set repeat off
password <your-issued-admin-password>
reboot
```

What each line does:

- **`set prv.key`** — pins the radio's Ed25519 keypair to a specific
  seed so the pubkey (the radio's identity on the mesh) stays
  stable. If you ever re-flash, paste the same line and your
  observer entry on the dashboard continues uninterrupted.
- **`set radio 910.525,62.5,7,5`** — Austin Meshcore RF parameters:
  frequency `910.525 MHz`, bandwidth `62.5 kHz`, spreading factor
  `7`, coding rate `5`. Without matching values, the radio joins
  WiFi and MQTT fine but never overhears any LoRa traffic.
- **`set tx 22`** — TX power, 22 dBm (max for this hardware).
- **`set name`** — the radio's advertised name on the mesh. Other
  nodes will see this when your radio adverts.
- **`set wifi.ssid` / `set wifi.pwd`** — the only fields you have to
  edit. WiFi password accepts spaces on the rest of the line:
  `set wifi.pwd my super secret` works.
- **`set mqtt3.*`** — points the radio at our broker on slot 3. We
  use slot 3 so observers can keep slots 1–2 for other presets
  (LetsMesh etc.) if they want.
- **`set repeat off`** — the radio observes traffic and publishes
  it to MQTT, but does NOT forward floods over the air. This keeps
  the mesh's air time clean — most observer locations have plenty
  of repeaters around them already. If your deployment is in a
  coverage gap and the local mesh would benefit from another
  forwarder, you can flip this to `on` later.
- **`password`** — sets the radio's admin password, which gates any
  remote-management commands sent over the mesh later. Note: per
  the firmware docs, this requires the following `reboot` to take
  effect.
- **`reboot`** — restarts the radio so all settings take effect.

Paste the entire block. Each `set` line gets acknowledged in turn,
then the radio reboots.

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

```
get public.key
```

This is your radio's pubkey — share it with the operator so they can
find you on the dashboard.

You can also watch the live log stream — the firmware prints `MQTT
connected` (or equivalent) and then a brief log line per RX packet.

## 4. Operator confirms on the dashboard

Tell the operator your `get public.key` value. They'll run:

```
corescope/scripts/list-observers.sh
```

…and within ~30 seconds your row appears with `IATA = AUS`,
`1H_PKTS` ticking up, and `AGE` in seconds. That's confirmation the
end-to-end path is working.

## 4a. Re-flashing later (preserve your identity)

If you ever upgrade the firmware (say, a new release of the fork
fixes a bug), the new image starts with a fresh keypair by default —
which would make you appear on the dashboard as a brand new observer
with no history.

To keep continuity, save the `set prv.key <hex>` line from your
original paste block somewhere safe. After re-flashing, re-paste the
configuration block (or just the `set prv.key` line, then `reboot`).
The radio derives the same pubkey from the same seed, so the
dashboard treats it as the same observer continuing.

If you ever lose the seed and the radio is still booting fine, you
can read it back: `get prv.key`. Save the result.

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
  - The radio's RF parameters need to match the local mesh, or
    the radio can't overhear traffic. Verify with `get radio` —
    expected for AUS Meshcore: `910.525,62.5,7,5`. Re-run
    `set radio 910.525,62.5,7,5` if it differs.

- **`get wifi.status` shows connected, broker says authenticated, but
  the dashboard shows your observer with `1H_PKTS = 0`** — same root
  cause as above. The radio is online but not on the right
  frequency/SF/BW.

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
  Build targets are all named `*_repeater_observer_mqtt` — the radio
  becomes a repeater that also publishes RX to MQTT. There's no
  Companion-firmware build of the bridge; if your radio is currently
  acting as a Companion (paired to a phone), this firmware will
  replace that role.
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
