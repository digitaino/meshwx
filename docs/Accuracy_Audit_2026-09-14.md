# Accuracy audit against independent sources, 2026-09-14

The bot's answers, from the satellite feed on the Pi, checked line by line
against sources that do not come from EMWIN: `api.weather.gov` (active
alerts, latest observation, gridded forecast), the Iowa Environmental
Mesonet local-storm-report archive, `aviationweather.gov` raw METARs,
`forecast.weather.gov`'s copy of the PFM, and SWPC's JSON/text products.
Times are 19:50–20:00 UTC.

## Findings before the fixes

| Item | Bot said | Independent source | Verdict |
|---|---|---|---|
| `warn NY` | 1 active: Frost Adv til Tue 7:00AM | 1 alert, Frost Advisory, NWS Burlington, ends 08:00 EDT | Right event, wrong clock: rendered in the bot's Central time. |
| `storm NY` | 8 flash-flood reports in Westchester | IEM: those 8 reports exist, timed 12:15–14:38Z **on Sep 13**; 0 NY reports in the last 18 h | **Wrong.** LSR summary products are re-issued the next morning; the bot took product receipt time as report time. |
| `warn TX` | 4 active heat advisories | 7 heat advisories (BRO, CRP, EWX×2, FWD, HGX, SHV) + 1 SPS | **Wrong: 4 of 9 missing.** All four were issued more than 12 h earlier and had been expired from the store while still in force. |
| `wx round rock tx` obs | 97F dp66 S14g23 few (KEDC 16km) | METAR KEDC 141935Z 18012G20KT FEW065 36/19 | Correct (36 °C = 97 °F, 12 kt = 14 mph, 20 kt = 23 mph). |
| `forecast austin tx` | Tue 100/79, Wed 99/79, Thu 92/80, Fri 92/79, Sat 93/78 | PFM (same issuance, Camp Mabry): Tue 102/77, Wed 100/78, Thu 98/75, Fri 97/73, Sat 98/74; NWS grid Thu 98, Fri 98 | **Wrong by 2–6 °F.** Highs were the max of 3-hourly samples; the PFM's own Min/Max row was ignored. |
| `space` | K4 active; Kp now 2.0; G1 Thu | SWPC Kp 4.0 at 15:00Z; 3-day forecast issued 12:30Z: max past 24 h = 2, G1 expected, columns Sep 14–16 | K4 and the numbers correct; **G1 day wrong** (Sep 16 is Wednesday); "Kp now" mislabelled the 24-hour maximum. |
| `rain TX` | includes Albuquerque | Albuquerque is in NM; Midland's roundup has an "OTHER LOCATIONS IN NEW MEXICO" section | **Wrong** station in the list. |
| TX event areas | HGX HT.Y.11: 3 zones; EWX HT.Y.11: 6 zones | API: 25 and 16 zones (two segments each) | **Wrong for cities in the second segment:** only one segment's zones were kept per event. |
| `wx` (US) | 99 active | API: 106 land alerts, 220 marine | Not comparable one-to-one (events vs alerts, marine classification); no error found. |

## Fixes (all in commit "accuracy:" and the segment-merge commit that follows)

1. **Retention by product type.** Warning-class products stay 48 h and are
   retired by their VTEC expiry; LSRs 24 h; everything else 12 h. Applied to
   the store, the internet source and its cache, and the SDR directory scan.
2. **Storm reports by report time.** Each LSR line's date and time, in the
   product's own timezone, must fall inside the last 6 hours.
3. **PFM Min/Max row** supplies the daily high (evening column) and the
   following night's low (next morning column); the sampled temps remain the
   fallback.
4. **Space weather days** come from the "Kp index breakdown Sep 14-Sep 16"
   line; "Kp 24h max" is the label.
5. **Place-local times**: "8:00AM EDT" for a New York warning from a Texas
   bot; the abbreviation appears only when the zone differs from the bot's.
6. **RWR state sections** are honoured, so a Texas roundup's New Mexico
   section does not feed the Texas rain list.
7. **Segments merged**: every UGC of every segment of an event is kept.

## After the fixes (same sources, 20:00 UTC)

| Item | Bot | Source | |
|---|---|---|---|
| `warn TX` events | BRO SPS, BRO HT.Y.14, CRP 21, EWX 11, EWX 12, FWD 32, HGX 11, SHV 18 | identical set of 8 | match |
| `warn NY` | Frost Adv til Tue 8:00AM EDT | ends 08:00 EDT | match |
| `storm NY` | none in the last 6 h | IEM: none | match |
| `forecast austin tx` | Tue 102/77, Wed 100/78, Thu 98/75 20%, Fri 97/73, Sat 98/74 | PFM identical; grid within 1 °F | match |
| `space` | K4 active; Kp 24h max 2; G1 Wed | Kp 4.0; max 2; G1 Sep 16 | match |
| `rain TX` | no Albuquerque | | fixed |

## What this audit could not check

- Warnings that never arrived on the satellite (the feed drops ~1–10 % of
  products in busy hours; follow-up products usually recover them). A missed
  issuance with no follow-up is invisible to the bot and to this audit.
- Any place outside Texas and New York; the same code paths serve them.
- Tropical products: none active.

## Nationwide, automated: `scripts/audit.py`

The manual checks above became a script that runs every hour on the Pi
(`deploy/meshcore-weather-audit.timer`) and writes `data/audit.json`, which
the portal Overview shows. It compares, for every state, the set of VTEC
events and their zone lists with api.weather.gov and the last 6 hours of
storm reports with IEM; for 30 stations nationwide the raw METAR text with
aviationweather.gov; for 20 places the daily highs/lows with the same PFM
point on forecast.weather.gov (NWS grid as a loose fallback); and the space
weather numbers with SWPC.

Its first two runs found four more faults, all fixed the same day:

| Fault | Effect | Fix |
|---|---|---|
| Flood Watches (`FFA`) never extracted | missing in AZ, IA, KS, MO, NE, NM | product type added |
| Cancelled/expired/upgraded events resurrected by the older product that issued them | CYS high wind warning, PAH heat advisory still "active" | retired keys suppress older entries |
| First forecast day skipped by the downsampler while labelled by the service | Minneapolis showed Tuesday's 70/51 under "Mon" | the downsampler owns period dates; a day with a Min/Max max counts |
| LSR event names that fill their column ("Non-Tstm Wnd Gst") | every Billings wind report dropped (0 of 38) | fixed-width column parsing |
| PFM 6-hourly Date row glued to the row above it on the satellite copy | Houston's extended days dated two days early | glued-row repair extended |

Third run, 20:24 UTC: **154 of 155 checks pass**. Every state's warning set
and zone lists match NWS; every state's storm reports match IEM (Montana
38 of 38, South Dakota 7 of 7); all 30 METARs match; 19 of 20 forecasts are
identical to the online PFM for the same point (Anchorage has no PFM point
online and matched the grid within 6 °F); space weather matches. The one
failure was a severe thunderstorm warning in its last minutes that NWS had
already dropped while the expiring SVS was still in flight; the audit now
gives such events a 15-minute grace.

## Standing rule

The hourly audit is the acceptance test. A reply-format or parser change
ships only when the next audit run is clean, and a red Overview card is a
bug until proven to be a satellite gap.
