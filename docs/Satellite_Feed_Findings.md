# GOES-19 EMWIN Feed: First-Light Findings

Inspection of `mesh-wx.digitaino.com` on 2026-09-13, about 12 hours after the 1.7 GHz antenna went up. Written to decide what the v5 protocol and the bot can rely on from the satellite feed alone. Companion to `MeshWX_Airtime_Review.md`.

## 1. The station

| Item | Value |
|---|---|
| Host | Raspberry Pi 4, aarch64, 4 cores, 2 GB RAM, 29 GB SD (20 GB free), 43 °C |
| Receiver | goestools: `goesrecv` (RTL-SDR R820T, 1694.1 MHz, 2.4 Msps, gain 30 fixed, bias tee on for SAWbird+ GOES) → `goesproc -m packet` |
| Services | `goesrecv.service`, `goesproc.service`, `goes-dashboard.service` (Python, port 8080), `goes-cleanup.timer` (hourly; images 3 days, text 30 days, emergency purge at 80 % disk) |
| Output | `~/goes-images/emwin/YYYY-MM-DD/` for EMWIN; `goes19/` for ABI imagery; `dcs/`, `nws/`, `text/` handlers configured |
| CPU | goesrecv ~65 % of one core; load average 0.8 |

Two receive sessions so far: 03:14–03:59Z (204 files) and 13:30–13:49Z at the time of inspection (about 150 files). Between them the receiver was down (the journal shows abort loops at 23:16 local before a successful start).

### Signal margin

From the goesrecv monitor line (10 s samples, this boot):

| Window (local) | Viterbi avg | Packet drops | Packets / 10 s |
|---|---|---|---|
| 08:20 | 2239 | 100 % | 0 |
| 08:30 | 1426 | 51 % | 225 |
| 08:40 onward | ~470–500 | ~2 % | ~555 |

After the 08:40 adjustment the link is usable but not comfortable: a Viterbi average near 500 with drops in the low single-digit percent is a thin margin. Rain fade or a small pointing shift will push it back into losses. Worth trying: gain 35–40 instead of 30, a finer aim on the dish, and confirming the SAWbird is actually powered (bias tee on, LNA LED). Target is a Viterbi average under 400 with drops at 0.

## 2. What arrives

### File format

goesproc's `emwin` handler writes files named exactly like the NOAA internet bundle, for example:

```
A_WWUS76KLOX130322_C_KWIN_20260913032209_270249-1-NPWLOXCA.TXT
```

WMO header, issuing station, issue time, KWIN receive timestamp, KWIN sequence number, and the 8-character AWIPS product ID with state. The bot's existing `EMWIN_FILENAME_RE`, `EMWIN_ID_RE`, and `EMWIN_TS_RE` match without changes. File bodies are byte-identical in structure to the internet bundle (`\r\r\n` line endings, WMO line, AFOS line, `$$` terminators). The internet bundle URL the bot uses today, `DC.gsatR`, is itself the GOES-R EMWIN stream, so the satellite is the same product set, not a subset by design.

### Rate and volume

| Measure | Value |
|---|---|
| Satellite, good-signal minutes (13:40–13:48Z) | ~9 text products / minute |
| Internet bundle, 12:00–12:59Z | 11.6 text products / minute (698 / hour) |
| Text volume | ~3.3 MB per 40 min ≈ 120 MB / day |
| Graphics (regional radar GIFs, `RAD*`) | ~32 per 40 min, 1.4 MB; about 15-minute cadence per region |

The per-minute gap between satellite and internet is partly hour-of-day and partly the ~2 % packet loss compounded by multi-packet files. A same-hour comparison against the 13Z internet bundle is in section 5.

### Latency

Issue time (WMO header) to KWIN timestamp is 0–2 minutes; KWIN timestamp to file on disk is 5–60 seconds. Warnings arrive fast enough for send-on-ingest.

### Product mix (40 minutes, all offices)

SAH METAR collectives dominate (about a quarter of files), then MIS, RRM, OBS, PFM, ZFP, RWR, TAF, LSR, AFM, AFD, river products, CLI, SFT. Warning-class products seen in the sample: SVR, SVS, NPW, MWW, FLS, FFS, FFA, CFW. Also present: NOW, FWF, HWO-class (HWF), SWO-class not yet observed, SPS not yet observed. The window is too short to say anything is absent; the internet 12Z hour bundle shows FFW, FLS, NPW, SPS, LSR, AFD, TAF, RWR, CLI, SFT all flowing at their normal rates.

### Austin coverage

In 40 minutes, no EWX products were issued (nothing to receive). Texas offices did appear (HGX climate reports). METAR collectives already carried Austin-area stations: KEDC, KGTU, KHYI, KSAT, KLZZ, KRYW, KT82, K3T5. KAUS was not in the sample, which is consistent with its :53 hourly cadence falling outside both windows. A full day of capture is needed before drawing conclusions about EWX product cadence, but there is no reason to expect it to differ from the internet feed.

### Radar

The HRIT EMWIN stream carries the regional NWS radar mosaic GIFs (`RADSTHES`, `RADSTHPL`, `RADGRTLK`, etc.) roughly every 15 minutes. They are what the old radar product would have needed, and they are not georeferenced or transparent, exactly as previously found. Nothing in v5 depends on them.

## 3. End-to-end test against the bot's real code

All 351 satellite text files from the two sessions were run through the bot's own fetcher parser and `WeatherStore.ingest`, then through pyIEM warning extraction and the METAR and PFM encoders, inside the running container.

| Step | Result |
|---|---|
| `InternetSource._parse_emwin_file` | 351 / 351 parsed |
| `WeatherStore.ingest` | 351 products, 40 product types |
| pyIEM `extract_active_warnings` | 3 still-active VTEC warnings found (WI.Y LOX, SC.Y GRR, BH.S GRR); the rest had expired by test time |
| `warnings_to_binary` | packed at 61, 67, 53 bytes |
| `encode_metar` on a satellite SAH line | 16 bytes |
| PFM products present | 17, from 16 offices |

Conclusion: the parser, store, pyIEM path, and encoders work unchanged on satellite data. The SDR source is a directory watcher, nothing more.

**One trap, found the hard way.** Opening the files in Python text mode turns EMWIN's `\r\r\n` into `\n\n`, which shifts the AFOS line and makes pyIEM fail with "Could not locate AFOS Identifier" on every warning. The watcher must read bytes and decode, exactly as the zip fetcher does today.

## 4. What this means for v5

- **The hot set is fully available from the satellite**: warnings with VTEC and polygons, METARs for local stations, PFM for local points, ZFP as fallback, HWO, LSR, NOW, FWF, CLI. Nothing in the v5 proposal needs the internet.
- **Send-on-ingest is realistic.** Warnings land within about a minute of issue; the 5-minute scheduler tick should go.
- **Loss handling belongs in the station, not the protocol.** At 2 % packet drop most multi-packet products still arrive, but a warning lost at the satellite hop is lost for good. The v5 digest already covers clients that missed a broadcast; the same idea does not exist for the bot itself. Two mitigations: improve the RF margin (section 1), and keep a per-office "last seen product time" check so the bot can flag when EWX has gone quiet for longer than normal.
- **Retention is already handled** on the Pi (30 days of text). The bot's own 12-hour store window stays as is.
- **Port 8080 is taken** by the goes dashboard. The bot's portal must move (e.g. 8081) when it runs on this Pi.
- **No radar product.** Confirmed again that the GIFs are the wrong shape for the mesh. Leave radar out of v5.

## 5. Same-hour completeness check

Matched by KWIN sequence number against the NOAA 1-hour text bundle for 13:00–13:59Z, fetched at 14:02Z.

| Window | Internet text files | Also received by satellite | Missed by satellite | Completeness | Satellite-only files |
|---|---|---|---|---|---|
| 13:40–13:59Z (after the aim fix, ~2 % packet drops) | 198 | 190 | 8 | **96.0 %** | 130 |
| 13:30–13:59Z (includes the 50 %-drop minutes) | 294 | 224 | 70 | 76.2 % | 147 |

The 8 misses in the good window were 3 MIS, 2 SAH, 1 OBS, 1 RWR, and **1 NPW** (a non-precipitation warning product). Per minute the satellite matched the internet count in 15 of 20 minutes and was short by 1–3 files in the other 5.

Two conclusions:

- At the current margin, expect roughly **4 % of text products to be lost**, and that loss is blind to product importance. Over a stormy day that is a real chance of missing a warning issuance, which is why the RF margin work in section 1 matters more than any protocol feature.
- The satellite delivered **130 files in 20 minutes that the internet text bundle did not contain** (mostly RWR, SAH, ZFP, PFM, river products). The satellite is not a subset of the internet feed; for text products it is at least as complete. There is nothing the bot gets from the internet that it cannot get from the dish.

## 6. Next actions

1. Let the receiver run for 24 hours untouched, then repeat the product census with a full day: EWX product cadence, KAUS METAR cadence, and daily completeness against the internet hourly bundles.
2. Improve the RF margin: try gain 35–40, re-aim, confirm LNA power. Re-check the Viterbi average.
3. Implement `SDRSource` as a directory watcher over `~/goes-images/emwin/*/`, reading bytes, keyed by filename like the zip path, with the same 12-hour expiry.
4. Add a per-office silence monitor to the portal (last product time per WFO in coverage).
5. Move the portal port when the bot is deployed on the Pi.

## 7. Second look, 14:22Z (after the signal was improved)

Receiver now: Viterbi average ~150, packet drops ~0.9 %, 561 packets / 10 s, steady for 40 minutes. That is a good lock.

### Integrity

- **224 files present on both the satellite and the 13Z internet bundle were byte-identical.** No corruption, no truncation. Files that lack a `$$`/`NNNN` terminator (MIS, RWR, RVF, CF6, LSR) are the product's normal format, not damage.
- **Zero duplicate bodies** across 1,036 files. Products that appear twice with the same AWIPS ID (e.g. `RWRFWDTX` at 14:01Z twice) are distinct WMO headers, not repeats.
- 2-minute spot check at 14:20Z: 28 of 28 internet files received.

### Timing

| Measure | Median | p90 | Worst |
|---|---|---|---|
| Issue (WMO header) → KWIN | 0.5 min | 2 min | 60 min (re-sends of older products) |
| KWIN → file on disk | 7 s | 28 s | 67 s |

KAUS's 13:53Z METAR was on disk at 13:56Z.

### Rate

Over 13:50–14:21Z the satellite averaged **21.6 text files per minute**, ranging from 5 to 99. The 99-file minute was 14:10Z: the hourly burst of RWR roundups, CF6 climate tables, and OBS collectives. The ingest path must expect bursts of ~100 files in one minute at :00–:15 past each hour. Median file is 2.5 KB, p90 13 KB, largest 180 KB (an RRM river product).

### Product census, 1,036 text files

Top: SAH 244, RWR 157, MIS 141, RRM 49, RVF 37, OBS 36, PFM 34, ZFP 30, TAF 30, RVM 29, LSR 21, AFM 17, AFD 12, SFT 8, CLI 7, FWF 5, HWO 3, NOW 2, SPS 2. Warning-class: FFW 3, MWW 3, FLS 2, NPW 2, CFW 2, SVR 1, SVS 1, FFA 1, FFS 1, FLW 1, MWS 1. 66 GIFs across 17 regional radar/satellite image IDs.

### Texas and Austin

EWX so far: only `RWREWXTX` (14:01Z and 14:10Z). FWD 8, HGX 15 (CF6 climate), SJT 3, CRP 1, LUB, MAF. No EWX forecast, discussion, or warning has been *issued* during the receive windows, so nothing to judge yet. SAH collectives carry a median of 15 METARs per file; 5,777 METAR lines in 1,036 files, Austin-area stations included.

### What the warnings tell us about v5

Across 11 active VTEC warnings nationwide at 14:22Z:

- UGCs were 38 zones and **9 counties**; 4 of the 11 warnings (three FF.W from OKX, one FL.W) have counties only and would be forced onto the polygon format today. County support in the v5 zone list is required, not optional.
- pyIEM exposes the SBW tags directly: the ICT SVR carried `wind=60`, `hail=0.75`; the OKX FFWs carried `FLASH FLOOD: OBSERVED` / `RADAR AND GAUGE INDICATED` and `DAMAGE THREAT: CONSIDERABLE`. The v5 warning message should carry hail (¼ in units), wind (mph), tornado tag (2 bits), **and flood source (2 bits) + flood damage threat (2 bits)** — the FFW tags were missing from the earlier draft.
- A non-VTEC SPS from TOP with a polygon parsed and would broadcast as a polygon warning; the SPS path works on satellite data.

### Additions to next actions

- Size the watcher's ingest for 100-file bursts at the top of the hour.
- Add flood tags to the v5 warning layout.
- Add county polygons to the client bundle (already planned) and county UGC runs to the zone list encoding.

## 8. Third look, 19:20Z: EWX's first full package, and three real bugs

Receiver: Viterbi ~150, 0 drops, ~1,000–1,280 text files per hour through the afternoon. 6,181 files on disk.

### EWX cadence (UTC)

`RWR` at :01 and :10 every hour; river products (`RRM`/`RVA`/`HYD`) around :26; `NPW` (heat advisory) 17:20; the afternoon forecast package **18:49–18:51: AFD, FWF, ZFP, PFM, SFT** plus a spot forecast (`FWS`) at 18:36. No HWO, SPS, NOW, or LSR from EWX yet today. KAUS METARs arrive in the SAH collectives; the KAUS TAF arrives inside the `TAFALLUS` collective.

### Completeness in a busy hour, with a perfect link

18Z internet bundle vs satellite, matched by product identity (WMO id + station + issue time + AWIPS id, since sequence numbers are reassigned when NWS re-queues a product):

| | Internet | Truly missing on satellite |
|---|---|---|
| All text products | 791 | 86 (**89.1 % delivered**) |
| SAH | 208 | 3 |
| RWR | 87 | 6 |
| AFD | 24 | 6 |
| TAF | 25 | 3 |
| MWW | 8 | **4** |
| NPW | 7 | 1 |
| LSR | 6 | 1 |
| FLS | 5 | 1 |
| FFS | 2 | 1 |
| SFT | 22 | 19 |
| HWO, SPS, NOW, ZFP, FFW, FFA, SVS, RFW, FWF | 15 | 0 |

With zero packet drops at the receiver, these losses are upstream: the HRIT EMWIN channel is bandwidth-limited and NWS sheds products under load, preferentially the large ones (SFT, AFD) but not exclusively (half the marine warnings that hour). In the quiet 13:40 window delivery was 96 %. **Plan on roughly 90 % delivery of any given product in busy hours, and on missing the occasional warning issuance.** The satellite also delivered 352 products in that hour that the internet text bundle did not carry (ZFP 75, PFM 73, RWR 65, FWF 24, AFM 24), so neither feed is a superset.

Consequence for the system: derive warning events from **every** product that carries VTEC, not only the initial issuance. A missed `FFW` is recovered from its `FFS` follow-up minutes later; a missed `SVR` from its `SVS`. The extractor already scans SVS/FFS/FLS, so this mostly holds today, but the delta logic must treat "first seen via a follow-up" as NEW, not CHANGED.

### Bug 1: multi-VTEC segments lose events (real, hit today)

The EWX heat advisory at 17:20Z had one segment with two VTEC lines: `NEW HT.Y.0011` (Monday noon–7 PM) and `CON HT.Y.0010` (until 7 PM today). `_segment_to_entry()` in `protocol/warnings.py` reads `seg.vtec[0]` only, so the advisory that was actually in effect this afternoon was dropped and only tomorrow's was broadcast. Fix: loop over `seg.vtec` and emit one entry per event.

### Bug 2: one malformed forecast point aborts the whole PFM (real, hit today)

`PFMEWXTX` at 18:51Z has, in the Hondo Airport block, a UTC header row with two columns run together: `... 09 1215 18 ...`. The internet copy is byte-identical, so this is an NWS formatting glitch. `parse_pfm()` raises `ValueError: hour must be in 0..23` from `_build_slot_times()` and the exception escapes for the entire product, so **Austin got no forecast from this issuance**. Fix: parse each point in its own try/except, validate hours to 0–23, and skip the bad point. Production logs show no "PFM parse failed" lines, which is a logging gap rather than evidence it never happens.

### Bug 3: `AWW` is not an SPC watch

`_WARNING_PRODUCT_TYPES` includes `AWW` commented as "Area Weather Watch — SPC". It is the Airport Weather Warning (Birmingham issued seven today for MGM). It carries no VTEC, so it is silently ignored, but the comment and inclusion are wrong. SPC watches arrive as `SEL`/`WOU`/`WCN`; none were issued today, so that path is still untested on satellite data.

### The heat advisory as a v5 worked example

Today's wire: 101 bytes (16 zones × 3 + 11-byte header + headline).
v5: zones `TXZ173, 191–194, 205–209, 220–225` are 4 runs × 4 bytes = 16, plus the 13-byte header = **29 bytes**, exactly one byte under a 2-block AES boundary. Two VTEC events → two messages. Text line for the plain-app channel: `HEAT ADV til 7PM. Travis,Hays,Williamson,Bexar +12 zones. heat index 111`.

### SPC outlooks

`SWODY1`/`SWODY2` arrive (16:41Z, 17:31Z). pyIEM's SPC parser needs a PostGIS database for county attribution, so it cannot run on the Pi as is. The outlook text carries the categorical lat/lon polygons in its tail; a small standalone parser could produce a 1-byte "day-1 risk at coverage center" product. Worth doing later; not a v5 blocker.

### Additions to next actions

- Fix bugs 1 and 2 now; they affect the current bot regardless of v5.
- Log PFM parse failures at WARNING, not DEBUG.
- Delta logic: first sighting via a follow-up statement counts as NEW.
- Remove `AWW` from the warning product set; add `SEL`/`WOU` testing when a watch is issued.

## 9. Fixes applied (2026-09-13, uncommitted in the working tree)

| Bug | Fix | Test |
|---|---|---|
| Multi-VTEC segments dropped all but the first event | `_extract_warnings_pyiem` now emits one entry per VTEC line; `_segment_to_entry` takes the event and picks the matching headline | `tests/test_warnings.py` (synthetic two-event NPW modeled on KEWX 17:19Z) |
| One malformed PFM point aborted the whole product | `parse_pfm` wraps each point; `_parse_hour_tokens` splits run-together hours (`1215` → `12 15`) and rejects anything outside 0–23 | `tests/test_pfm.py::TestMalformedPoint`, `TestRealEWXProduct` (real KEWX product as a fixture) |
| PFM parse failures logged at DEBUG | now WARNING, with the zone | — |
| `AWW` listed as an SPC watch | removed; `WOU` added | — |
| **6-hourly PFM table never parsed** (found while testing): the row scan skipped past the next table's header, so every forecast had only the 3-hourly days | scan now stops *at* the next header | `test_both_tables_parsed_for_austin` |
| **6-hourly table stamped with the issue date** (exposed by the previous fix): days 4–7 landed on days 1–4 | tables are anchored on their own `Date` row via `_table_start_date`; UTC rollover of the first column handled | `test_six_hourly_table_anchored_on_its_date_row`, `test_seven_day_forecast_from_real_product` |

Effect, measured on the 274 PFM products received today: 215 now encode to 7 daily periods, 42 to 6, 17 to 3. Before the fixes the same products gave 2 periods (21 bytes on the wire, which is what the production log had been showing as "forecast-austin-tx → 21 bytes"). Austin's 18:51Z forecast now encodes to 56 bytes covering Mon–Sun.

Still open: 13 of ~1,500 points nationwide have other header glitches and are now skipped with a warning instead of killing their product; Alaska and Guam PFMs return no forecast (different coordinate/zone layout, not investigated); the 6-hourly table has no wind speed row, so days 4–7 carry wind 0.
