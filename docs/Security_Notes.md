# Security notes for the text bot (going live 2026-09-14)

What an attacker can reach, what the bot does about it, and what is left.

## Inputs the bot accepts

| Input | Who can send it | What happens to it |
|---|---|---|
| Channel text on `#meshwx` | anyone with the channel name (the key is derived from it) | sender name and text are stripped of control characters and capped (40 / 200 chars), then parsed by an anchored regex into a command and a location; the location is reduced to letters, digits, space `,.-'` and 50 chars before it reaches the resolver |
| DM | anyone who has the bot's advert | same cleaning; the sender is identified by public key, not by name |
| Binary requests (WXQ/MWX) | apps | decoded inside a try/except that catches every exception; a malformed frame is logged and dropped |
| Adverts | anyone | contact name cleaned; the contact table on the node is bounded by firmware, the bot's name→key map is capped |
| `@lat,lon` prefix | DM senders | parsed as floats, range-checked, capped table |
| EMWIN products | the satellite (unauthenticated broadcast) | every parser is wrapped per product or per point; a bad product is skipped, never fatal |
| Portal HTTP API | anyone on the LAN (no login by design) | see below |

Nothing the bot receives is ever passed to a shell, a SQL engine, `eval`,
or a template. Replies are built from parsed weather data and resolver
names; the only user text echoed back is the cleaned location string in
"Unknown location: …".

## Airtime abuse

Every reply costs the mesh. Budgets, all enforced before any work is done:

- one reply per sender per 5 s;
- at most 40 replies per sender per hour and 400 per hour in total;
- a stranger (no DM path) gets a channel reply only if the request arrived
  within 2 hops, at most one per 10 minutes per sender and 12 per hour;
- `reply_mode=channel` (every reply a flood) is a test setting, shown as a
  red badge on the Overview.

Per-sender state (rate limits, paging, learned names, cached locations) is
pruned so a flood of invented names cannot grow memory.

## Identity and spoofing

- A channel message carries the sender's *name* only. The bot maps it to a
  key through its contact list. Someone posting under another user's name
  can make the bot DM that user weather lines — bounded by the budgets,
  harmless in content.
- A DM is authenticated by the sender's key (ECDH). Admin commands
  (`contacts`, `remove`, `clear-contacts`, `advert`, `refresh`, `broadcast`)
  are accepted only from a sender whose public key starts with
  `MCW_ADMIN_KEY`. **Set it to at least 16 hex characters of your own key**
  (the Radio page shows every contact's key); an 8-character prefix is a
  32-bit search for anyone willing to grind keypairs.
- Other bots (`WX-*`) are ignored as command sources.

## Portal

The portal has no login on purpose; access control belongs at the edge
(LAN only, or a tunnel with its own auth). What it still guards against:

- **Cross-site requests.** Every POST/PUT/DELETE must carry
  `X-Requested-With: meshcore-portal`, a header a web page on another origin
  cannot add without a CORS preflight the server never grants. So a page
  someone on the LAN happens to open cannot flip transmit on, rewrite `.env`,
  or restart the bot through their browser.
- **`.env` writes** are limited to a whitelist of keys and to single-line
  printable values, so a value cannot smuggle a second `KEY=value`.
- **HTML injection.** Everything the portal renders from data (names, log
  lines, replies, headlines) goes through `escapeHtml` or `textContent`.
- Restart, reboot and reconnect are the only actions with side effects
  beyond settings; none spawns a shell with user input (`git rev-parse` is
  the one subprocess, fixed arguments).

## Residual risks, stated plainly

1. Anyone on the LAN can operate the portal. That is the chosen model.
2. A determined attacker can generate a keypair matching a short admin
   prefix. Use a long one.
3. The satellite feed is unauthenticated. A local transmitter on 1.694 GHz
   could feed the receiver forged products; the parsers will not crash, but
   the bot would repeat what it was fed. There is no fix for this in
   software short of cross-checking sources, which an offline deployment
   cannot do.
4. Channel names are public knowledge; anyone can post requests. The budgets
   bound the cost; region scope on the repeaters bounds the reach.
