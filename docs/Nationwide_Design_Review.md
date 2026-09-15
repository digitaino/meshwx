# One channel, many bots: adversarial review of the nationwide design

> **Status, 2026-09-15:**
> - Still in the code as reviewed (db766a5, 48a1e34, `main.py`): `#meshwx` for every bot, the reply modes, the stranger channel reply with its hop gate and budget (once per sender per 10 min, 12 an hour), and ignoring `WX-*` senders.
> - Done from the §4 to-do list: `set_time` on connect shipped in db766a5 itself (`radio.py`). The Overview shows red while `reply_mode=channel` (db766a5, kept by d5171e4).
> - Changed:
>   - MeshWX v5 (ad6dc24) sends broadcasts as GRP_DATA, specified on `#meshwx` (spec §2.1). The bot uses one slot when `MCW_MESHWX_CHANNEL` equals the text channel, which is now the `config.py` default (`#meshwx`).
>   - The nearest-bot rule covers only people's place commands sent as channel text; `>` requests and DMs have none (spec §12). It also skips peers that advertise 0,0, and this bot's own adverts carry 0,0 (spec §1).
> - Still open: `MCW_FLOOD_SCOPE` (#9, #11) and a country/band guard on transmit (#12). Neither exists in the code.
> - Where the current truth lives: `docs/MeshWX_v5_Spec.md` revision 3, §8.2 (request limits) and §12 (several bots).

2026-09-14. Written after the Austin bot went live on the Pi and the question
"should every bot have its own channel?" came up. Companion to section 7 of
`MeshWX_Airtime_Review.md`, which first proposed the shared channel; this is
the attack on it.

## 1. The design under review

- **One request channel for every bot, everywhere: `#meshwx`.** Binary
  broadcasts on `#meshwx-data` until GRP_DATA lets them share the channel.
  [2026-09-15: v5 GRP_DATA is specified on `#meshwx` (ad6dc24); see the
  status note on the default config.]
- **Replies are DMs.** A channel command from a sender the bot cannot DM gets
  one reply on the bot's own channel if it arrived within a few hops, plus an
  advert, so the next exchange can be a DM. Budgets: one per sender per
  10 min, twelve per hour. `reply_mode=channel` floods every reply (testing);
  `dm_only` never floods.
- **Bots advert as `WX-<IATA>` with coordinates.** Every bot learns its peers
  from those adverts. A request that names a place is answered only by the
  nearest bot to that place; requests without a place are answered by every
  bot that hears them, by DM. Bots ignore messages from `WX-*` senders.
  [2026-09-15: adverts carry 0,0 (spec §1); the nearest-bot rule applies
  only to people's channel text, not to `>` requests or DMs (spec §12).]
- **Containment is not the channel name.** Physical range, the repeaters' hop
  limits, MeshCore region scope, and each bot's coverage radius are what keep
  floods local.

## 2. How DMs actually work, because everything above leans on it

A MeshCore DM is encrypted with a shared secret derived from *both* public
keys. So:

- To **send** a DM the bot needs the user's public key. It gets it only from
  the user's advert (auto-add is on). Phones advert when the user taps
  "advert" or on the app's own schedule; many users never do.
- To **receive** a DM the phone needs the bot's public key, from the bot's
  advert. A bot that never adverts can never be DMed, and its DMs to the
  phone are dropped undecrypted.
- A channel message carries the sender's *name* only. The bot maps name to key
  through its contact list. No advert from that phone, no DM back.
- The first DM to a contact floods (no path yet); the ACK comes back with the
  path; after that DMs go direct along it. A stale path (a repeater moved) means
  one failed DM, then a flood again.

Consequences the code now honours: the bot adverts on connect, when transmit is
switched on, and every `MCW_ADVERT_INTERVAL_HOURS` (6); a stranger's channel
command triggers an advert if none went out in the last hour; the channel reply
to a stranger ends with "DM me for the rest" so they know to send their own
advert. What the code cannot fix: a user who never adverts only ever gets
channel replies, within the budget.

## 3. Attacks and failure modes

| # | Threat | What happens today | Mitigation in place | Residual risk |
|---|---|---|---|---|
| 1 | **Anyone can post to `#meshwx`.** The key is `SHA256("#meshwx")`; the name is public by design. | A troll sends `wx` requests in a loop. | Per-sender 5 s limit; stranger channel replies capped at 1/10 min/sender and 12/h; DM replies cost only the path. Requests without a place are answered by every bot in range: a flood of `help` from a spot heard by three bots costs three DMs each. | Amplification stays bounded by the budgets, but the budgets are per bot: N bots in RF range multiply it by N. Region scope, when adopted, bounds N. |
| 2 | **Name spoofing to aim DMs.** A channel message is `name: text`; the bot DMs whatever contact carries that name. | Someone posts as "Alice"; Alice gets weather DMs she did not ask for. | 5 s per-name limit. A DM reply reveals nothing private and costs Alice one packet. | Nuisance only. A DM-initiated request is cryptographically from its sender; channel-initiated DMs are best effort. If it becomes a problem, answer channel commands by DM only for contacts whose *path* was learned from their own DM. |
| 3 | **Two names, one contact list.** Two users named "Digitaino" in the bot's contacts. | `get_contact_by_name` returns the first; the DM may go to the wrong one. | Learned names are cached with the key; a failed DM forgets the mapping. | Rare; the wrong recipient gets a harmless weather line. |
| 4 | **Duplicate answers in overlap zones.** Austin and San Antonio both hear a request. | Place requests: only the nearest bot answers. Placeless requests: both DM. `reply_mode=channel`: both flood. | Nearest-bot rule with pubkey tie-break. Bots learn peers only from adverts they actually hear, which is exactly the set whose floods overlap theirs. | A bot that has not yet heard its neighbour's advert answers everything for up to one advert interval after boot. Placeless duplicates by DM are cheap and self-explanatory (the sender sees two bot names). Never run `channel` mode where meshes overlap. |
| 5 | **Bot-to-bot loops.** A bot's channel reply is itself a channel message. | Without the filter, bot B would parse "Round Rock, TX: 91F…" as a command. | Senders named `WX-*` are ignored. The NLP would not have matched most replies anyway. | An operator who names their bot without the prefix breaks the filter for everyone near them. The prefix is documented and is the default advert name. |
| 6 | **Repeater dedupe drops our replies.** Repeaters drop a payload whose hash they saw recently. | Two identical replies within the dedupe window: the second never propagates. | GRP_TXT carries the sender timestamp; identical text at different seconds hashes differently. Binary broadcasts carry a sequence byte. | None, as long as the clock on the node advances. A node with a dead RTC and no time set repeats timestamps: `set_time` on connect is worth adding. |
| 7 | **Hop-count gate is advisory.** `path_len` is what the packet says it crossed. | A stranger 6 hops away gets no channel reply; one at 1 hop does. | Only used to *decline*, never to grant something extra. | A forged low hop count gains at most one rate-limited channel reply. |
| 8 | **Channel slots.** Every phone has 8; `#meshwx` and `#meshwx-data` take two. | Per-city naming would take two per city visited. | One name nationwide. GRP_DATA later folds the data channel into `#meshwx`. | Communities that already use many channels lose one slot to weather. |
| 9 | **Regions arrive later (Austin has none yet).** A repeater with `region denyf *` drops unscoped floods; a bot sending scoped floods is dropped by repeaters that do not know the region. | Today everything is unscoped and forwarded. | `MCW_FLOOD_SCOPE` is the planned config: the bot must send in whatever scope the local repeaters carry (CMD 63). | The switch-over is a cliff: the day the repeaters turn on `denyf *`, an unscoped bot goes silent at hop 0 with no error. The console will show the bot sending and nobody answering. The operators must coordinate the day. |
| 10 | **Regions and peer discovery.** Adverts are floods; with scoping, a bot only hears peers inside its region. | Overlap detection shrinks to the region. | That is correct: floods that cannot cross a region boundary cannot overlap either. | Two regions that both cover Hays County will both broadcast its warnings; clients dedupe by event key. |
| 11 | **Wrong region key on the bot.** Scope names are public hashtags; a typo (`#atx` vs `#austin`) means every packet is dropped by every repeater. | n/a today. | Log the scope on connect; the Radio page should show it and the peers heard. | Same silent-failure shape as #9. |
| 12 | **Worldwide: different bands and rules.** EU bots on 869.525 MHz with 1 % duty cycle, US on 910.525 MHz. | A US preset applied to an EU node is illegal on air. | Presets are explicit and confirmed in the portal; the node reports its current parameters. | Nothing stops an operator applying the wrong one. The bot could refuse to enable TX when the frequency is outside its country's band, given a country setting. |
| 13 | **Worldwide: place names.** The resolver is US plus territories. | "wx Paris" resolves to Paris TX. | `ambiguous` flag and "add state" hint. | A non-US deployment needs its own gazetteer and a source other than EMWIN; out of scope, and the protocol does not care. |
| 14 | **Airtime budget of adverts.** N bots × one flood per 6 h. | 100 bots in RF connectivity would be 400 flood adverts a day across the whole mesh. | 6 h interval; adverts are ~100 bytes; a mesh with 100 bots in one RF domain does not exist and region scope would cut it. | Fine. |
| 15 | **A bot with no home.** `MCW_HOME_CITIES` empty. | Nearest-bot rule cannot compute distance. | It answers everything (safe default). | Two homeless bots overlap fully; document that home is required for multi-bot areas. |
| 16 | **`reply_mode=channel` left on by mistake.** | Every reply floods every repeater in range, forever. | The mode is visible on the Text Bot page and in the console line of every reply. | Add a nag: the Overview should show a warning badge while the mode is `channel`. |

## 4. What this review changes in the code

- Hop gate, reply modes, nearest-bot rule, bot-ignores-bot: implemented.
- `WX-<IATA>` naming with coordinates: convention; the Austin node is `WX-AUS`.
- To do: `MCW_FLOOD_SCOPE` (#9, #11), `set_time` on connect (#6), an Overview
  warning while `reply_mode=channel` (#16), a country/band guard on the
  transmit switch (#12).

## 5. The one thing that is not solvable in the bot

A user who has never adverted cannot be DMed by anyone, bot or human. The
channel fallback exists for exactly that person, and it is budgeted because it
is a flood. The honest instruction for users is: join `#meshwx`, send one
advert, then ask.
