# Delivery confirmation and retransmit: design

2026-09-15. Implemented the same day (commit 6af542e and the CoreScope
rule fix that followed). Everything under "facts" was
checked against the v1.17.1 firmware source, the meshcore-py library in the
venv, real packets on scope.digitaino.com, or the live radios. From "The
system" on, this describes the code as of 4387a71 (checked 2026-09-15).

## Where we are

> The state before 6af542e. MeshWX v5 (ad6dc24) has since retired the v4
> frames, FEC parity and beacons mentioned here.

The bot never checks whether anything it sends was heard.

- Channel replies (flood on the text channel): `send_chan_msg`, fire and
  forget (`radio.py: send_channel_message`).
- DMs: `send_msg`, fire and forget. The firmware answers with the ack code
  it expects and a suggested timeout, and the recipient's ACK later arrives
  as `PUSH_CODE_SEND_CONFIRMED` with the round-trip time. The bot ignores
  both (`radio.py: send_dm`). meshcore-py already ships
  `send_msg_with_retry` (wait for the ACK, retry up to 3 times, reset a
  stale direct path to flood after 2).
- Binary broadcasts and beacons: fire and forget. v4 frames carry FEC
  parity for some products, which helps with corruption, not with a
  packet nobody repeated.

A person in a chat app who hears no repeat of their message sends it
again. The bot should do the same, with a budget.

## Facts the design rests on

1. **The node reports every packet it hears, including repeats of its own.**
   `Dispatcher::checkRecv` calls `logRxRaw` before the packet is parsed and
   before the seen-table check; the companion's `MyMesh::logRxRaw` pushes
   `PUSH_CODE_LOG_RX_DATA` (0x88) whenever serial is connected, no flag. So
   when a repeater repeats our packet, the app layer drops it as already
   seen but the RX log still delivers it, with SNR, RSSI and the path bytes
   (which repeater carried it). Verified live: the Mac bot's companion radio
   (v1.14.1) has published 654k RX-log packets to scope.digitaino.com as
   "Digitaino Central Observer". This settles the old note that companion
   firmware was silent: it is not.
2. **The packet hash ignores header and path.** `Packet::calculatePacketHash`
   is SHA-256 over the payload-type byte and the payload, first 8 bytes. An
   echo therefore has the same hash as what we sent. CoreScope's `hash`
   field is exactly this value (checked on real packets).
3. **We can compute the bytes we send.** A channel packet payload is
   `sha256(secret)[0] || HMAC-SHA256(secret, ct)[:2] || ct` where `ct` is
   AES-128-ECB(secret) over `timestamp(4, LE) || flags(1) || "name: text"`
   zero-padded to 16 bytes. Verified by decrypting real public-channel
   packets from CoreScope with pycryptodome, which is already installed as a
   meshcore-py dependency. The node gives us the secret (`get_channel`,
   already used by the portal) and the name; `send_chan_msg` lets us choose
   the timestamp. So the bot knows the hash of a reply before it sends it.
4. **Every MeshCore node dedupes by that hash.** A retransmission with
   identical bytes is dropped by every node that already has it and
   accepted by every node that missed it. Nobody sees the message twice.
   That is exactly the semantics we want, and it is why the retransmit must
   reuse the same timestamp. (Firmware marks packets seen on transmit but
   never refuses to transmit one; only reception is filtered.)
5. **DMs have an end-to-end signal.** `MSG_SENT` returns `expected_ack` and
   `suggested_timeout`; the recipient's ACK arrives as `EventType.ACK` with
   that code and the round-trip milliseconds.
6. **Airtime.** US preset (SF7, 62.5 kHz, CR 4/5): 30 B ≈ 160 ms, 60 B ≈
   240 ms, 110 B ≈ 390 ms, 170 B ≈ 570 ms. One retransmit doubles the cost
   of one reply, and only when no repeater carried it.
7. **CoreScope.** `/api/packets?timeRange=15m&type=<payload type>&limit=200`
   returns recent packets with `hash`, `observation_count`, `observer_name`
   and `resolved_path`; `/api/packets/{id}` lists every observer that heard
   it. The `search=` parameter does not match hashes, so lookup is a time
   window filtered by hash on our side. Channel packets are encrypted, so
   CoreScope cannot attribute them to WX-AUS; the hash is the only key.
   (Found later: the server ignores `timeRange`. The lookup relies on the
   newest-first order instead and asks for 300 packets.)

## The system

### DeliveryTracker (`meshcore/delivery.py`)

One object for the life of the process, shared by every radio connection.
The radio hands it every `RX_LOG_DATA` frame (always, not only when MQTT is
on) and every `ACK`.

Every tracked send registers an `Outbound` record: kind (`channel_text`,
`channel_data` or `dm`), the packet hash (none for a DM), a resend function
that repeats the send exactly, sent time, attempt number, the DM ack code
if any, and a reference to the traffic-log event.

- On `RX_LOG_DATA`: parse header and path, hash payload-type + payload. If
  it matches an outstanding record and `path_len ≥ 1`, mark it **echoed**
  with the echo delay, the path bytes (repeater hashes) and the echo's SNR.
  Separately, any packet from anyone with `path_len ≥ 1` updates
  `last_repeat_heard_at`: proof that some repeater is in range.
- On `ACK`: match the code, mark **acked** with the round-trip time.
- A record's timer fires after the **echo window** (`MCW_ECHO_WINDOW_S`,
  default 8 s; for a DM the firmware's `suggested_timeout × 1.2`, kept
  between 3 and 30 s). Time the bot's own event loop spent blocked is added
  back, up to one more window: a handler that ran late is no proof that
  nobody repeated us. No echo and no ack means **retransmit**, after
  0.5–2 s of jitter, up to `MCW_RETRANSMIT_MAX` times (default 1), unless:
  transmit is off; the per-hour retransmit budget is spent; no repeat from
  anyone was heard in the last 10 minutes once the bot has been up that
  long (nothing would change, and the user's own "no repeater in range"
  case); or the CoreScope check (below) says a repeated copy was observed.
  With `MCW_RETRANSMIT_MAX=0` it only measures.
- The outcome lands on the traffic-log event as `delivery: {result, echo,
  echo_ms, via, snr, acked, rtt_ms, attempts, resent, skipped,
  observed_by, ...}` so the feed, the counters and the public page can show
  it. `echo_ms` and `rtt_ms` are timed from the transmission that was
  heard, not from the first send. The last 24 hours of outcomes survive a
  restart (`data/delivery_outcomes.json`).

### Per kind

- **Channel text** (every reply in `reply_mode=channel`, which the Pi runs
  on 2026-09-15, and otherwise the one channel reply a stranger gets):
  compute the hash before sending, register, send with an explicit
  timestamp, resend with the same timestamp. Echoes on the Austin mesh
  normally come back within about 4 s.
- **DM**: register the ack code from `MSG_SENT`. Resend on no ack; the
  resend carries the next attempt number, so the firmware expects a new ack
  code, which replaces the old one. If the DM was never acked and the
  contact has a direct path, reset the path to flood (what
  `send_msg_with_retry` does) so the next message does not die on a stale
  route. DMs are matched on the ACK only: the bot does not compute a DM's
  hash, so it cannot tell "echoed but not acked" from "not even echoed".
- **Channel datagrams** (`GRP_DATA`: every v5 broadcast, every answer to an
  app request, and the portal's link test): the same echo logic, always on,
  with no separate switch. The scheduler stamps the v5 `seq` into the packet
  before it goes to the radio, so a resend repeats the stamped bytes under
  the same `seq`. The `seq` counter is saved in `data/warning_state.json`
  and continues after a restart. A resend waits only for the radio's send
  lock, so it can go out after later packets of the same batch.
- **Adverts** are not tracked.

### CoreScope correlation (optional, internet)

Off unless `MCW_SCOPE_URL` is set (e.g. `https://scope.digitaino.com`).
Every lookup is fail-soft with a 3 s timeout and never in the send path. It
pulls the newest 300 packets of that payload type, finds our hash, and reads
every observation of it.

- `MCW_SCOPE_MODE=decide`: when the echo window passes with no local echo,
  one query. Only observers whose copy carries a repeater in its path
  count (`repeated_by`); an observer next door that heard us at zero hops
  proves nothing. At least `MCW_SCOPE_MIN_OBSERVERS` (default 2) of them
  are needed before the retransmit is skipped. Catches the case where our
  node did not hear the repeat but the mesh did. This probe runs seconds
  after the send, while observers are still reporting, so it only ever
  vetoes a resend and never goes on the record.
- `MCW_SCOPE_MODE=stats` (the default): no probe.
- In both modes, 45 s after a send has settled, one query records how many
  observers heard it, how many heard a repeated copy, and through which
  repeaters. A thinner answer never replaces a fuller one.

This is optional, and weather data never depends on it.

### What the operator sees

- Traffic feed, per reply: `echo 1.2 s via D0,3A`, `ack 2.4 s`,
  `resent ×1`, `no echo`, `no ack`, `no echo · not resent: <reason>`, and,
  once CoreScope has answered, the number of observers whose copy came
  through a repeater (paths in the tooltip).
- Radio › Health tiles: "Heard" (share of tracked sends echoed or acked in
  the last hour, with sent and resent), "Echo" (median echo delay, and the
  24 h share), "Last heard", "Unheard streak", "Loop lag". The Overview
  says how many replies were heard back.
- Settings: Text Bot has "Resend if not heard, max" (0 measure only, 1, 2),
  "Echo window (s)" and the per-hour budget; System › Settings has the
  CoreScope URL, mode and minimum observers.

### Configuration

```
MCW_RETRANSMIT_MAX=1            # 0 turns the whole thing into measurement only
MCW_ECHO_WINDOW_S=8
MCW_RETRANSMIT_PER_HOUR=30
MCW_MESH_QUIET_S=600            # no repeat heard from anyone this long: no resend
MCW_SCOPE_URL=                  # e.g. https://scope.digitaino.com
MCW_SCOPE_MODE=stats            # stats | decide
MCW_SCOPE_MIN_OBSERVERS=2       # observers of a REPEATED copy before decide mode skips a resend
```

## Verify before trusting it

The plan as written before the first run. The measurement has since moved
the echo window from 5 s to 8 s (`docs/Radio_Swap.md`).

1. Measurement only first (`MCW_RETRANSMIT_MAX=0`) for a day on the Pi:
   confirm echoes match, and read the echo-delay distribution to set the
   window from data rather than a guess.
2. Confirm on two phones that an identical retransmit shows once on a phone
   that got the first copy and shows up on one that missed it.
3. Confirm a DM retry does not show twice on the recipient's phone (the
   attempt counter is inside the plaintext, so the hash differs; the
   companion app is expected to dedupe on sender and timestamp).
4. Check the CoreScope window query finds our hashes within the 15-minute
   range and how long after transmission they appear.

## Effort

`delivery.py` (~250 lines), hooks in `radio.py` send paths and event
subscriptions, three new fields on traffic events, feed and tile changes in
the portal, config keys, tests with recorded RX-log frames. About half a
day, plus the measurement day.
