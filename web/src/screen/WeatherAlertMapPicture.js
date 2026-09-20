// Port of MC1Services/Sources/MC1Services/Services/Weather/Screen/WeatherAlertMapPicture.swift
//
// Revision 10 turned the *national* alert map into the alert map: a phone can hold last hour's
// country and this minute's Texas at the same time, and both are true. What the map draws is
// therefore no longer "the sweep" but a picture assembled out of several — and the whole of the
// difficulty is in one sentence of the design:
//
//   for each state the newest sweep whose scope includes it wins, and only that sweep's entries
//   for that state are drawn.
//
// Without that rule a phone that asked for Texas at 13:40 and held the country from 13:20 would
// draw both, and every Texas alert that ended in those twenty minutes would still be on the map,
// under this hour's Texas alerts, indistinguishable from them.

import { MeshWXTables } from '../meshwx/index.js'
import { WeatherAreaSweepAssembly } from '../weather/index.js'

/**
 * What the alert map draws and what its status card says (docs/MESHWX_UI.md §17):
 * `{ parts, entries, coversWholeCountry }`.
 *
 * - `parts`: one per sweep held, newest first — one status line each. A `Part` is
 *   `{ group, builtAt, includesAdvisories, wasCut, isScoped, scope, stateCodes, receivedPackets,
 *   totalPackets, missingIndexes, firstReceivedAt, lastReceivedAt }`.
 *   `stateCodes` is what this sweep is the newest word on, **empty for a national sweep** —
 *   which the card reads as "the rest of the country", since the states it is not the newest
 *   word on are named by the scoped parts above it.
 * - `entries`: the sweep entries to draw, each carrying `part`, the index of the part it came
 *   from, so the map can say which line a shape belongs to.
 * - `coversWholeCountry`: a national sweep is held. With none, "an unshaded state outside the
 *   scope is unknown, never clear" is the whole claim the card may make.
 */
export const WeatherAlertMapPicture = Object.freeze({
  /** Nothing held: the map before anybody has tapped for one, which is most of the time. */
  empty: Object.freeze({
    parts: Object.freeze([]), entries: Object.freeze([]), coversWholeCountry: false,
  }),

  /**
   * `sweeps` is `WeatherBotState.areaSweeps`, newest first. `states` is `index.json` `states`,
   * for naming a scope's indices.
   *
   * `now` is taken and not read: every time in the picture is an instant the caller formats
   * against its own clock, and a sweep does not go stale — an hour-old map of the country is an
   * hour-old map of the country, which is what the status line says. It is in the signature
   * because the design writes it there, and because a rule that may one day drop a sweep for age
   * must not change every caller when it does.
   */
  make({ sweeps, states = MeshWXTables.shared?.states ?? [], now }) {
    void now
    const ordered = [...(sweeps ?? [])].sort((lhs, rhs) => rhs.builtMinutes - lhs.builtMinutes)
    if (ordered.length === 0) return WeatherAlertMapPicture.empty

    // Which part is the newest word on each state. `ordered` is newest first, so the first part
    // that covers a state wins it. A scoped sweep whose packet 0 never arrived covers nothing:
    // it cannot name a state, so it cannot be the newest word on one.
    const winnerByState = new Map()
    ordered.forEach((sweep, index) => {
      if (!sweep.isScoped) {
        // A national sweep is the newest word on every state nothing above it has claimed.
        for (const entry of WeatherAreaSweepAssembly.entries(sweep)) {
          if (!winnerByState.has(entry.state)) winnerByState.set(entry.state, index)
        }
        return
      }
      for (const state of sweep.scope ?? []) {
        if (!winnerByState.has(state)) winnerByState.set(state, index)
      }
    })
    // A national sweep speaks for every state nothing newer named, including the ones with
    // nothing to draw — which is exactly what lets the card say "clear" about them at all.
    const nationalIndex = ordered.findIndex((sweep) => !sweep.isScoped)

    const wonStates = ordered.map(() => [])
    for (const [state, index] of winnerByState) wonStates[index].push(state)

    const parts = ordered.map((sweep, index) => ({
      group: sweep.group,
      builtAt: WeatherAreaSweepAssembly.builtAt(sweep),
      includesAdvisories: sweep.includesAdvisories,
      wasCut: sweep.wasCut,
      isScoped: sweep.isScoped,
      scope: sweep.isScoped ? (sweep.scope ?? null) : [],
      // The national sweep names no states: it is "the rest of the country", and the scoped
      // parts above it have already named what it is not the newest word on.
      stateCodes: sweep.isScoped
        ? wonStates[index].map((state) => states[state]).filter((code) => code != null).sort()
        : [],
      receivedPackets: WeatherAreaSweepAssembly.receivedPacketCount(sweep),
      totalPackets: sweep.total,
      missingIndexes: WeatherAreaSweepAssembly.missingIndexes(sweep),
      firstReceivedAt: sweep.firstReceivedAt,
      lastReceivedAt: sweep.lastReceivedAt,
    }))

    const entries = []
    ordered.forEach((sweep, index) => {
      // A scoped sweep of unknown scope "contributes its entries but wins no state": it is not
      // the newest word on anywhere, so it takes nothing from anybody, and what it does say is
      // still the only picture this phone has of those areas.
      const isUnplaced = sweep.isScoped && sweep.scope == null
      for (const entry of WeatherAreaSweepAssembly.entries(sweep)) {
        if (!isUnplaced && winnerByState.get(entry.state) !== index) continue
        entries.push({ ...entry, part: index })
      }
    })

    return { parts, entries, coversWholeCountry: nationalIndex >= 0 }
  },

  /**
   * The newest national part, or null. What the cost estimate measures against, and what the
   * card reads to know whether it may say anything at all about a state nothing named.
   */
  nationalPart(picture) {
    return picture.parts.find((part) => !part.isScoped) ?? null
  },

  /** Whether any part is still missing packets, so the card offers to ask for them. */
  isPartial(picture) {
    return picture.parts.some((part) => part.missingIndexes.length > 0)
  },
})
