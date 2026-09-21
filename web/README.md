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
| [Node](https://nodejs.org) | One download, the green LTS button. It is what hands the files to your browser |

## Start it

1. Download **meshwx-web.zip** from the
   [latest release](https://github.com/digitaino/meshwx/releases/latest), and unzip it.
2. Double-click **start-macos.command**, or **start-windows.bat** on Windows.
   The first time, macOS will say it is from an unidentified developer: right-click the file,
   choose **Open**, then **Open** again. It asks once and never again.
3. Your browser opens at `http://localhost:8137`. Press **No radio** at the top, then **Connect
   over Bluetooth** or **Connect over USB**, and pick your radio.

Add a place, press **Update**, and the weather comes in over the air. Closing the terminal window
that opened stops MeshWX. Nothing is installed and nothing keeps running.

On Linux, or if you would rather type it: `node serve.mjs` in the folder, or
`python3 -m http.server 8137`.

## Nothing is arriving

A radio hears only the radios that are on **exactly** the same frequency, bandwidth, spreading
factor and coding rate, and a radio out of the box is on the firmware's own default rather than on
your mesh. It still connects, still answers, still names itself, so nothing looks wrong.

Press your radio's name at the top, then **Radio settings**. The Radio card shows **Heard since
connecting**. While that says Nothing, pick your region under **Preset**, press **Apply radio
settings**, and watch it start counting.

## Have a look without a radio

`http://localhost:8137/?link=demo` replays a stormy Austin morning that a bot really sent: the
alerts, the conditions, the forecast, the map and three radar tiles. No radio is involved and
nothing is transmitted.

## Questions

**Does it need the internet?** No, at no point. Everything it draws with is in the folder. A
laptop that has never been online runs all of it, which is the entire reason this exists.

**Then why does it start a server?** Because a browser will not let a page opened straight from a
folder load its own code or reach a radio. `serve.mjs` hands the folder to your browser at
`localhost` and does nothing else: no internet, nothing listening to the network, nothing left
behind when you close the window.

**Can I use it on my phone?** Not this. On an iPhone, MeshWX is an app, which needs none of this.

**Is anything sent anywhere?** No. There is no account, no analytics, no telemetry and no
connection to anything but your own radio. The places you add and the weather you receive stay in
your browser.

**Where does the weather come from?** A weather bot on your mesh, which receives it from the
Weather Service over a satellite dish. No internet there either. That side is
[meshcore-weather](../README.md).

## For developers

Running it from a checkout, the packaging tool, the screens and the porting rules are in
[docs/DEVELOPING.md](docs/DEVELOPING.md), [docs/PORTING.md](docs/PORTING.md) and
[docs/UI_KIT.md](docs/UI_KIT.md).
