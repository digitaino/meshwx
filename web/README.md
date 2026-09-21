# MeshWX

Severe weather alerts, current conditions, the forecast and radar on your computer, from a weather
radio on your MeshCore mesh. It runs in your browser, and it never touches the internet: the maps,
the place names, the ZIP codes and the words in eleven languages are all in the download.

## What you need

| | |
|---|---|
| A computer | macOS, Windows, Linux or Chrome OS |
| Chrome, Edge, Brave or Vivaldi | Safari and Firefox cannot talk to a radio |
| A MeshCore radio | Firmware 1.15 or newer, on USB or paired over Bluetooth, and not connected to your phone's MeshCore app at the same time |
| A weather bot in range | Any node named `WX-` something on the `#meshwx` channel |

Nothing else. Nothing to install, no terminal, and no internet at any point.

## Start it

1. Download **MeshWX.html** from the
   [latest release](https://github.com/digitaino/meshwx/releases/latest).
2. Double-click it. It opens in your usual browser: if that is Safari or Firefox, right-click the
   file instead, choose **Open With**, and pick Chrome.
3. Press **No radio** at the top, then **Connect over Bluetooth** or **Connect over USB**, and
   pick your radio.

Add a place, press **Update**, and the weather comes in over the air.

It is one file of about 20 MB, because the maps, the place names, the ZIP codes, the forecast
zones and the words in eleven languages are all inside it. Keep it anywhere; it works from a stick,
and it works on a machine that has never been online.

## Nothing is arriving

A radio hears only the radios that are on **exactly** the same frequency, bandwidth, spreading
factor and coding rate, and a radio out of the box is on the firmware's own default rather than on
your mesh. It still connects, still answers, still names itself, so nothing looks wrong.

Press your radio's name at the top, then **Radio settings**. The Radio card shows **Heard since
connecting**. While that says Nothing, pick your region under **Preset**, press **Apply radio
settings**, and watch it start counting.

## Have a look without a radio

Press **No radio** at the top and choose **Show recorded data**, under "Without a radio". It
replays a stormy Austin morning that a bot really sent: the alerts, the conditions, the forecast,
the map and three radar tiles. Nothing is transmitted and no radio is involved.

## Questions

**Does it need the internet?** No, at no point. Everything it draws with is in the folder. A
laptop that has never been online runs all of it, which is the entire reason this exists.

**Why is it one big file?** Because a browser will not let a page opened from a folder read the
files beside it. Bluetooth and USB it will allow, so everything the client needs is inside the
page instead, and nothing is fetched at all.

**Is anything sent anywhere?** No. There is no account, no analytics, no telemetry and no
connection to anything but your own radio. The places you add and the weather you receive stay in
your browser.

**Where does the weather come from?** A weather bot on your mesh, which receives it from the
Weather Service over a satellite dish. No internet there either. That side is
[meshcore-weather](../README.md).

## The folder version

`meshwx-web.zip` on the same release page is the same client as a folder of ordinary files, with a
small server to hand it to the browser. It wants Node or Python installed and a command, and it is
there for anyone who would rather serve the files, put them on a web server, or read them. The
one-file download is the same code with the imports flattened and the tables embedded.

## For developers

Running it from a checkout, the packaging tool, the screens and the porting rules are in
[docs/DEVELOPING.md](docs/DEVELOPING.md), [docs/PORTING.md](docs/PORTING.md) and
[docs/UI_KIT.md](docs/UI_KIT.md).
