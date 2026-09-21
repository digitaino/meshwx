# MeshWX

Severe weather alerts, current conditions, the forecast and radar on your computer, from a weather
radio on your MeshCore mesh. There is no internet in this: the weather arrives over LoRa from a
`WX-` bot on the `#meshwx` channel, through a MeshCore radio connected to this computer by
Bluetooth or USB. Every name, table and map outline it is drawn with is already in this folder.

## What you need

- **A computer, and a Chromium browser on it.** Chrome, Edge, Brave or Vivaldi on macOS, Windows,
  Linux or Chrome OS. Safari and Firefox cannot talk to a radio.
- **A MeshCore companion radio**, firmware 1.15 or newer, on Bluetooth or USB. A radio talks to one
  companion at a time, so disconnect it from your phone's MeshCore app first.
- **A weather bot within reach** on the `#meshwx` channel. Its name starts with `WX-`.
- **[Node](https://nodejs.org)**, the green LTS button. It is what hands these files to your
  browser. Nothing else is installed.

You can look around without a radio: see "Try it with recorded weather".

## Start it

Double-click **start-macos.command**, or **start-windows.bat** on Windows.

The first time, macOS will say it is from an unidentified developer: right-click the file, choose
**Open**, then **Open** again. It asks once.

A terminal window opens and your browser goes to **http://localhost:8137**. Closing that window
stops MeshWX. Nothing keeps running and nothing was installed.

If you would rather type it, or you are on Linux:

```
node serve.mjs
```

No Node? Anything that serves a folder will do:

```
python3 -m http.server 8137
```

Then open **http://localhost:8137** yourself.

The address has to be `localhost`. A browser hands Bluetooth and USB only to a page it considers
secure, which means `https://` or `http://localhost`; these same files opened from the Finder
(`file://`) will not load at all.

## The first connect

1. **No radio** at the top of the page opens the radio panel. **Connect over Bluetooth** or
   **Connect over USB**, then pick your radio from the browser's own list.
2. The same panel has **Radio settings**. Open it if the radio is connected and nothing arrives: a
   radio hears only the radios on exactly the same frequency, bandwidth, spreading factor and
   coding rate, and a new one is on the firmware's default rather than on your mesh. Choose your
   region under Preset, press **Apply radio settings**, and watch **Heard since connecting** start
   counting. While it says Nothing, those four values are what to check.
3. Add your places. **Update** asks the bot. The answer is one LoRa packet, sent on the channel, so
   every MeshWX within reach gets it too.

Asking costs airtime, so ask when you want to know something rather than on a timer. Warnings the
bot broadcasts arrive without being asked.

## Try it with recorded weather

**http://localhost:8137/?link=demo** replays what the Austin bot sent one stormy morning, with the
times moved to now: alerts, conditions, the forecast, the map and three radar tiles. No radio is
involved, and nothing is transmitted.

## Offline, and on the home screen

After the first visit the browser keeps the whole client, so it works with no network at all. In
Chrome's menu, **Cast, save and share → Install page as app** makes it a window of its own.

## There is no internet in any of this

`serve.mjs` listens on 127.0.0.1 and hands out the files in this folder. Nothing here holds the
address of anything on the internet, and the page never asks for one: a laptop in airplane mode,
for a week, runs all of it. The only radio in the story is the one on your desk.

Getting this folder to somebody needs no network either. It is one 5.7 MB file: a USB stick, an SD
card, AirDrop, a share on the local network, or a download from a Pi on the mesh over plain
`http://`. Downloading a file does not have to be secure; only the page that asks for Bluetooth
does, and by then it is coming from `localhost` on the machine it runs on.

This does not go on a phone. A phone cannot serve itself `localhost`, and no phone browser hands
out Bluetooth over plain `http://`, so a phone wants an address rather than a folder.

## What is in here

```
index.html, src/, styles/, strings/   the client, and the words in 11 languages
data/                                 the tables the weather is drawn with: places, ZIP codes,
                                      forecast zones and counties, offices, stations, and the
                                      zone and county outlines for the map
assets/                               the offline basemap and the icons
demo/                                 the recorded morning
serve.mjs                             hands this folder to the browser, and nothing else
start-macos.command, start-windows.bat   double-click either one; they run serve.mjs
```

Nothing here calls out to the internet, there is no account, no analytics and no telemetry, and the
places you add and the weather you receive stay in this browser's own storage.

## Where this comes from

MeshWX is part of [meshwx](https://github.com/digitaino/meshwx), which is also the bot the weather
comes from. The iOS app is
[DigitainoMesh](https://github.com/digitaino/DigitainoMesh). Both are Apache 2.0.
