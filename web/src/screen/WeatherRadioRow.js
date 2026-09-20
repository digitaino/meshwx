// Port of MC1Services/Sources/MC1Services/Services/Weather/Screen/WeatherRadioRow.swift

import { WeatherStoredDigest } from '../weather/index.js'
import { WeatherAlertStatus } from './WeatherAlerts.js'

/**
 * The row at the foot of every place page (docs/MESHWX_UI.md §10):
 *
 *     WX-AUS · heard 2 min ago · alerts as of 8:02 PM  ›
 *
 * It is the only thing on the page that says anything about the alert list, and it is the way to
 * the radio page. The page above it says nothing about alerts on a quiet day — no check, no status
 * line, no "none for your location" — so the honesty the page gave up lives here: the row **turns
 * orange** when the alert list is old or missing, or when this phone missed messages from the
 * radio. Silence on the page is then never silence everywhere; it is one row away from being
 * accounted for.
 *
 * A row is `{ heardAt, listBuiltAt, missedMessages, listIsOld }`:
 *
 * - `heardAt`: live traffic only — a backlog drained from your radio's queue at connect is not the
 *   radio being in range now. Null with nothing heard live this session.
 * - `listBuiltAt`: when the alert list was built, on the bot's clock. Null when none has arrived.
 * - `missedMessages`: a gap, a warning the list named that never arrived, or an unfinished upgrade.
 * - `listIsOld`: the list is older than its three-hour cadence allows, or none has arrived at all.
 *
 * The Swift memberwise initialiser is not exported: `make` is the Swift's own static factory, and
 * its defaults (`heardAt` nil, `listIsOld` true) are what a row with nothing behind it reads as.
 */
export const WeatherRadioRow = Object.freeze({
  /**
   * Orange. Nothing else on the page changes colour with the weather data, so this reads as the
   * one thing worth opening.
   */
  needsAttention(row) {
    return row.missedMessages || row.listIsOld
  },

  make({ source, state, now }) {
    const digest = state?.digest ?? null
    return {
      heardAt: source?.lastLiveHeardAt ?? null,
      listBuiltAt: digest == null ? null : WeatherStoredDigest.builtAt(digest),
      missedMessages:
        state == null
          ? false
          : state.needsDigest ||
            (state.missingFromDigest ?? []).length > 0 ||
            Object.keys(state.pendingUpgrades ?? {}).length > 0,
      // No list at all is not "fresh": until one arrives the phone cannot tell whether anything is
      // active, and the row is the only place that now says so.
      listIsOld:
        digest == null ? true : (now - WeatherStoredDigest.builtAt(digest)) / 1000 > WeatherAlertStatus.listFreshFor,
    }
  },
})
