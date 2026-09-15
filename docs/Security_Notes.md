# Security notes for the text bot (going live 2026-09-14)

What an attacker can reach, what the bot does about it, and what is left.
Checked against the code on 2026-09-15.

## Inputs the bot accepts

| Input | Who can send it | What happens to it |
|---|---|---|
| Channel text on `#meshwx` | anyone with the channel name (the key is derived from it) | sender name and text are stripped of control characters and capped (40 / 200 chars), then parsed by an anchored regex into a command and a location; the location is reduced to letters, digits, space `,.-'` and 50 chars before it reaches the resolver |
| DM | anyone who has the bot's advert | same cleaning; the sender is identified by public key, not by name |
| App requests (`>` prefix) | anyone, by DM or on `#meshwx` | same control-character cleaning; the first word picks the answer and the rest is looked up as a warning identity, a UGC code, an ICAO code, a point index or a place. An unknown word gets a Not available message, and an exception while building an answer becomes a Not available (bot error) |
| Adverts | anyone | contact name cleaned; the contact table on the node is bounded by firmware, the bot's name→key map is capped |
| `@lat,lon` prefix | DM senders | parsed as floats, range-checked, capped table |
| `sat` / `>sat` | anyone | answered from the receiver dashboard sample the bot already holds, with no request to goesrecv: lock, signal quality, dropped packets, goesproc state, age of the newest EMWIN file. Nothing about the host, the network or the settings |
| EMWIN products | the satellite (unauthenticated broadcast) | every parser is wrapped per product or per point; a bad product is skipped, never fatal |
| Portal HTTP API | anyone who can reach the portal port (8081 on the Pi; no login by design) | see below |
| Public dashboard | anyone the goestools dashboard is exposed to | read-only; see below |

Nothing the bot receives is ever passed to a shell, a SQL engine, `eval`,
or a template. Replies are built from parsed weather data and resolver
names; the only user text echoed back is a location string, in "Unknown
location: …" and in the note after the last page of a reply. For text
commands that string has had the 50-character reduction above; for `>storm`
and `>rain` it has only had control characters removed.

## Airtime abuse

Every reply costs the mesh. Budgets, all enforced before a reply is built:

- text commands: one reply per sender per 5 s (2 s for `more`); at most 40
  replies per sender per hour and 400 per hour in total;
- a phone's resend of a DM request never meets the 5 s spacing. A resend
  that gets nothing costs nothing. One that sends something (a reply that
  was never acknowledged, or a request the spacing had dropped) counts
  against the hourly budgets, which can refuse it;
- DM replies go one at a time per contact, at most 5 waiting, each message
  tried at most 3 times (attempts 0 and 1, then 2 by flood). A failed reply
  goes again only when a resend of its request arrives;
- a `>` request resent by DM is answered again only 12 s after the last
  answer went out;
- app `>` requests: one per sender per 5 s, and 60 answer packets per hour
  across all senders (packets, not requests). A `>` request sent as a DM
  first passes the text limits above and counts against them; a `>` line on
  the channel does not;
- a stranger (no DM path) gets a channel reply only if the request arrived
  within 2 hops, at most one per 10 minutes per sender and 12 per hour;
- a channel packet no repeater echoed is sent again at most 30 times per
  hour in total (DM tries are bounded by the DM rules above instead);
- `reply_mode=channel` (every reply a flood) is a test setting, shown as a
  red dot in the portal's status strip.

Per-sender state (rate limits, paging, learned names, cached locations,
recent DM requests: 32 per sender, 5000 senders) is pruned so a flood of
invented names cannot grow memory. One exception: the
app request limiter keeps each sender's last request time until the bot
restarts, so invented names on `>` channel lines each leave a small entry.

## Identity and spoofing

- A channel message carries the sender's *name* only. The bot maps it to a
  key through its contact list. Someone posting under another user's name
  can make the bot DM that user weather lines — bounded by the budgets,
  harmless in content.
- A DM is authenticated by the sender's key (ECDH). Admin commands
  (`contacts`, `remove`, `clear-contacts`, `advert`, `refresh`, `broadcast`,
  `warnings-broadcast`, `test-data-ch`, `admin`) are accepted only from a
  sender whose key starts with `MCW_ADMIN_KEY`. The firmware reports a DM's
  sender as the first 6 bytes of its key, so the bot compares at most **12
  hex characters**. Set `MCW_ADMIN_KEY` to exactly the first 12 of your own
  key (the Radio page shows every contact's key): a longer value never
  matches, and an 8-character prefix is a 32-bit search for anyone willing
  to grind keypairs.
- Other bots (`WX-*`) are ignored as command sources on the channel, text
  and `>` lines alike. A DM from one is handled like anyone's.

## Portal

The portal has no login on purpose; access control belongs at the edge
(LAN only, or a tunnel with its own auth). What it still guards against:

- **Cross-site requests.** Every POST/PUT/DELETE must carry
  `X-Requested-With: meshcore-portal`, a header a web page on another origin
  cannot add without a CORS preflight the server never grants. So a page
  someone on the LAN happens to open cannot flip transmit on, rewrite `.env`,
  or restart the bot through their browser.
- **`.env` writes** are limited to a whitelist of keys, to single-line
  printable values of at most 200 characters, and to checked numbers and
  choices, so a value cannot smuggle a second `KEY=value` or leave a file
  the next start refuses.
- **HTML injection.** Everything the portal renders from data (names, log
  lines, replies, headlines) goes through `escapeHtml` or `textContent`.
- **Side effects.** Besides settings, the portal can restart the bot,
  reboot or reconnect the node, switch transmit, send a test datagram or an
  advert, change channels and radio parameters, remove contacts, adopt a
  replacement radio or start a new profile, run and edit broadcast jobs,
  and switch the dish to pointing mode, which stops goesrecv. None spawns a
  shell with user input (`git rev-parse` is the one subprocess, fixed
  arguments).

## Public dashboard

The goestools dashboard (`deploy/goes-dashboard/dashboard.py`, port 8080
on the Pi) is the public page. It treats a request as public when it
carries Cloudflare's headers or comes from an address outside its LAN
prefixes; a public viewer gets a read-only page, and every POST is refused.

For the bot it proxies one portal endpoint, `/api/public/bot`, fetched at
most every 3 s however many viewers there are. That bundle holds the bot's
name, public key and advertised position, the radio preset, channels,
coverage, the command list, counters and the recent traffic in redacted
form: channel traffic as anyone on `#meshwx` heard it, DMs as the command
and reply length only, admin and console traffic not at all. No settings,
no contacts, no other node's key (peer bots appear by name and position).
The portal serves the same endpoint on its own port, like the rest of it.

## What the traffic log writes to disk

Beside the lifetime counters (`traffic_stats.json` in the data directory),
`traffic_recent.json` keeps a week of per-message tallies (time, kind,
command, and the sender's channel name or the first 12 hex of a DM sender's
key) and the newest 300 events, so the rolling counters, the reply latency
and the live feed survive a restart. The events are what the portal's Text
Bot page shows, without the text of anything received or sent by DM (DM
requests, DM replies, admin commands): that text is only ever held in
memory. After a restart an old DM shows its command and reply length, as on
the public page.

## Residual risks, stated plainly

1. Anyone who can reach the portal can operate it, including pointing mode,
   which stops reception. That is the chosen model.
2. A determined attacker can generate a keypair matching the admin prefix.
   The most the bot can compare is 12 hex characters (48 bits); use all 12.
3. The satellite feed is unauthenticated. A local transmitter on 1.694 GHz
   could feed the receiver forged products; the parsers will not crash, but
   the bot would repeat what it was fed. There is no fix for this in
   software short of cross-checking sources, which an offline deployment
   cannot do.
4. Channel names are public knowledge; anyone can post requests, text or
   `>`. The budgets bound the cost; region scope on the repeaters bounds the
   reach.
5. The dashboard's LAN test compares text prefixes, and `172.2` also matches
   public addresses (172.2.x.x, 172.200.x.x and up). Such an address
   reaching port 8080 directly, not through Cloudflare, gets the controls.
