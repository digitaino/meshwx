# MeshWX web

The MeshWX weather client for Chromium browsers (Chrome, Edge, Brave, and Chrome on Android):
severe weather alerts, current conditions and the forecast from a `WX-` weather bot on the
`#meshwx` channel, received through a MeshCore companion radio over **Web Bluetooth** or
**Web Serial**. Nothing is fetched from the internet. After the first visit the page works
offline.

It is a port, layer for layer, of the weather tool in the iOS app, and follows the same
documents: the wire spec (`../docs/MeshWX_v5_Spec.md`), the test vectors
(`../docs/meshwx_v5_vectors.json`) and the screen spec in the app repository
(`docs/MESHWX_UI.md`). The porting rules are in `docs/PORTING.md`.

## Run it

```
node tools/dev-server.mjs          # http://localhost:8137
node --test test/                  # the whole suite
```

No build step, no dependencies. `http://localhost` is a secure context, which Web Bluetooth and
Web Serial require; anywhere else it has to be served over https.

The dev server maps `/data/` to `../meshcore_weather/client_data/`, the same bundle the bot
reads, and proxies `/api/bridge/` to the bot's debug bridge when a token file is present
(`web/.bridge-token`, ignored by git, or `MESHWX_BRIDGE_TOKEN_FILE`). The token is added
server-side and never reaches the page.

## Three ways to get weather into it

| Link | What it is |
|---|---|
| Bluetooth, USB | A MeshCore companion radio, firmware 1.15 or newer. A radio has one companion at a time: disconnect it from the phone first |
| Development bridge | The bot's own feed of what it transmits, through the dev server. Requests go out on the real air |
| Recorded data | `demo/datagrams.json`, what WX-AUS sent one morning, replayed with its times moved to now |

## Layout

```
src/meshwx    wire codec, bundle tables, place names, ZIPs, outlines     (pure)
src/weather   per-bot state, reducer, requests and retries, the service  (pure)
src/screen    what a screen says: alerts, conditions, coverage, plans    (pure)
src/radio     MeshCore companion protocol, Web Bluetooth and Web Serial
src/link      the service's transports: radio, bridge, replay
src/app       the tool's model, copy and formatting, the connection, boot
src/ui        screens; src/ui/kit is the whole UI framework
strings/      the iOS app's Weather.strings in 11 languages (tools/strings-to-json.mjs)
assets/       basemap.json, state outlines for the offline map (tools/build-basemap.mjs)
```

The pure layers run under Node and carry the ported test suites.
