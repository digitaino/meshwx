// Port of MC1ServicesTests/Weather/WeatherStateReducerTests.swift (docs/PORTING.md).
//
// Every rule in spec §2.3 (duplicates, gaps), §3–§5 (warnings, cancel, digest), §6–§7
// (newest wins) and §8.1 (text reassembly), one test each.

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { MeshWXRadar, MeshWXWire } from '../src/meshwx/index.js'
import {
  InMemoryWeatherStateStore,
  KeyValueWeatherStateStore,
  WeatherAreaSweepAssembly,
  WeatherBotState,
  WeatherStateChange,
  WeatherStateReducer,
  WeatherStoredDigest,
  WeatherStoredForecast,
  WeatherStoredObservation,
  WeatherStoredRadarTile,
  WeatherStoredWarning,
  WeatherTextAssembly,
  dateFromUnixMinutes,
  identityKey,
  weatherStateSnapshot
} from '../src/weather/index.js'
import * as F from './helpers/weather-fixtures.js'

/**
 * The Swift takes `state` `inout`; this port returns it (PORTING.md §3), so the suites hold one
 * of these and read `bot.state` where the Swift reads `state`.
 */
class Bot {
  constructor(botID = F.botID) {
    this.state = WeatherBotState.make({ botID })
  }

  apply(message, receivedAt) {
    const result = WeatherStateReducer.apply(message, { to: this.state, receivedAt })
    this.state = result.state
    return result.changes
  }
}

const minutes = (value) => value * 60 * 1000
const held = (state, identity) => state.warnings[identityKey(identity)]

describe('WeatherStateReducer', () => {
  // MARK: - Sequence

  it('the first message is accepted without a gap', () => {
    const bot = new Bot()
    const changes = bot.apply(F.warning({ seq: 17 }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.warningStored(F.svw42, { replacedExisting: false })])
    assert.equal(bot.state.lastSeq, 17)
    assert.equal(bot.state.lastHeardAt, F.t0)
    assert.equal(bot.state.needsDigest, false)
  })

  it('the same message twice is a duplicate and changes nothing', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 17 }), F.t0)
    const before = bot.state
    // The bot's resend of an unechoed packet, 9 s later.
    const changes = bot.apply(F.warning({ seq: 17 }), F.t0 + 9000)
    assert.deepStrictEqual(changes, [WeatherStateChange.duplicate({ seq: 17 })])
    assert.deepStrictEqual(bot.state, before)
  })

  it('a skipped seq is a gap that asks for the digest, and wrapping 255 to 0 is not', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 255 }), F.t0)
    let changes = bot.apply(F.warning({ seq: 0, identity: F.svw43 }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.warningStored(F.svw43, { replacedExisting: false })])
    assert.equal(bot.state.needsDigest, false)

    changes = bot.apply(F.warning({ seq: 3, identity: F.wsw7 }), F.t0)
    assert.deepStrictEqual(changes[0], WeatherStateChange.sequenceGap({ expected: 1, received: 3 }))
    assert.equal(bot.state.needsDigest, true)
    assert.equal(bot.state.lastSeq, 3)
  })

  // MARK: - Warnings

  it('a warning with a known identity replaces the stored one whatever the flag says', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, windMph: 60 }), F.t0)
    const changes = bot.apply(
      F.warning({ seq: 2, expiresMinutes: F.t0Minutes + 90, isUpdate: false, windMph: 70 }),
      F.t0 + 60_000
    )
    assert.deepStrictEqual(changes, [WeatherStateChange.warningStored(F.svw42, { replacedExisting: true })])
    assert.equal(Object.keys(bot.state.warnings).length, 1)
    const stored = held(bot.state, F.svw42)
    assert.equal(stored.warning.wind_mph, 70)
    assert.equal(stored.warning.expires_min, F.t0Minutes + 90)
    assert.equal(stored.updateCount, 1)
    assert.equal(stored.receivedAt, F.t0 + 60_000)
  })

  // Spec §3 and §10.5: a warning says when NWS issued it, and that is not when the packet
  // arrived. A replacement carries its own; one that carries none — an older bot — leaves the
  // known time alone, since an identity's issuance never moves.
  it("a warning's issue time is stored, replaced, and never erased by a message without one", () => {
    const bot = new Bot()
    // Heard 21 minutes after NWS issued it, which is the line the phone must show.
    bot.apply(F.warning({ seq: 1, issuedMinutes: F.t0Minutes - 21 }), F.t0 + minutes(180))
    let stored = held(bot.state, F.svw42)
    assert.equal(stored.issuedAt, dateFromUnixMinutes(F.t0Minutes - 21))
    assert.notEqual(stored.issuedAt, stored.receivedAt, 'three hours out of range does not restamp it')

    // A re-issued product under the same identity brings its own time.
    bot.apply(F.warning({ seq: 2, isUpdate: true, issuedMinutes: F.t0Minutes - 5 }), F.t0 + minutes(181))
    stored = held(bot.state, F.svw42)
    assert.equal(stored.issuedAt, dateFromUnixMinutes(F.t0Minutes - 5))

    // The revision 4 form of the same warning: no issue time on the wire, and the one already
    // known stands rather than being wiped.
    bot.apply(F.warning({ seq: 3, isUpdate: true }), F.t0 + minutes(182))
    stored = held(bot.state, F.svw42)
    assert.equal(stored.issuedAt, dateFromUnixMinutes(F.t0Minutes - 5))
    assert.equal(stored.warning.issued_min, null, 'the message itself carried none')
  })

  // The wire states the issue time as minutes *before the expiry*, and a digest may extend that
  // expiry — so the stored instant is resolved once, on arrival, and does not walk forward with
  // it. The decoded message already holds the absolute minute (PORTING.md §5), so where the Swift
  // proves the relative field would have walked, this proves it did not move at all.
  it('a digest extending the expiry leaves the issue time where it was', () => {
    const bot = new Bot()
    bot.apply(
      F.warning({ seq: 1, expiresMinutes: F.t0Minutes + 45, issuedMinutes: F.t0Minutes - 21 }),
      F.t0
    )
    bot.apply(F.digest({ seq: 2, nowMinutes: F.t0Minutes, entries: [[F.svw42, 120]] }), F.t0)
    const stored = held(bot.state, F.svw42)
    assert.equal(stored.warning.expires_min, F.t0Minutes + 120, 'the digest extended it')
    assert.equal(stored.issuedAt, dateFromUnixMinutes(F.t0Minutes - 21))
    assert.equal(stored.warning.issued_min, F.t0Minutes - 21)
  })

  it('a cancel removes the identity and says why', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1 }), F.t0)
    const changes = bot.apply(F.cancel({ seq: 2, reason: 2 }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.warningRemoved(F.svw42, { reason: 2 })])
    assert.deepStrictEqual(bot.state.warnings, {})
  })

  it('a cancel for an unknown identity is reported and harmless', () => {
    const bot = new Bot()
    const changes = bot.apply(F.cancel({ seq: 1, identity: F.wsw7 }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.cancelForUnknown(F.wsw7)])
  })

  it("expiry is judged by the phone's clock", () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, expiresMinutes: F.t0Minutes + 45 }), F.t0)
    const stored = held(bot.state, F.svw42)
    assert.equal(WeatherStoredWarning.isExpired(stored, { at: F.t0 + minutes(44) }), false)
    assert.equal(WeatherStoredWarning.isExpired(stored, { at: F.t0 + minutes(45) }), true)
    assert.equal(
      WeatherBotState.activeWarnings(bot.state, { at: F.t0, severity: () => null }).length, 1
    )
    assert.equal(
      WeatherBotState.activeWarnings(bot.state, { at: F.t0 + 3_600_000, severity: () => null }).length, 0
    )
  })

  it('active warnings sort by severity then soonest expiry', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, identity: F.svw42, expiresMinutes: F.t0Minutes + 45 }), F.t0)
    bot.apply(F.warning({ seq: 2, identity: F.svw43, expiresMinutes: F.t0Minutes + 20 }), F.t0)
    bot.apply(F.warning({ seq: 3, identity: F.wsw7, expiresMinutes: F.t0Minutes + 5 }), F.t0)
    // Pretend the winter storm (event 24) is a lower severity than the thunderstorms (event 3).
    const ordered = WeatherBotState.activeWarnings(bot.state, {
      at: F.t0, severity: (event) => (event === 3 ? 'warning' : 'advisory')
    })
    assert.deepStrictEqual(
      ordered.map((stored) => WeatherStoredWarning.identity(stored)),
      [F.svw43, F.svw42, F.wsw7]
    )
  })

  it('pruning drops only warnings expired before the cutoff', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, identity: F.svw42, expiresMinutes: F.t0Minutes + 45 }), F.t0)
    bot.apply(F.warning({ seq: 2, identity: F.svw43, expiresMinutes: F.t0Minutes + 200 }), F.t0)
    const { state, expired } = WeatherStateReducer.pruneExpired(bot.state, {
      expiredBefore: F.t0 + minutes(100)
    })
    assert.deepStrictEqual(expired, [F.svw42])
    assert.ok(identityKey(F.svw43) in state.warnings)
  })

  // MARK: - Digest

  it('a digest removes what it omits, lists what the app lacks, and clears the gap flag', () => {
    const bot = new Bot()
    // An hour before the list was built, so the list speaks for both.
    const earlier = F.t0 - 3_600_000
    bot.apply(F.warning({ seq: 1, identity: F.svw42 }), earlier)
    bot.apply(F.warning({ seq: 5, identity: F.wsw7 }), earlier)
    assert.equal(bot.state.needsDigest, true)

    const changes = bot.apply(
      F.digest({ seq: 6, entries: [[F.svw42, 45], [F.svw43, 20]] }),
      F.t0
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.digestApplied({ missing: [F.svw43], removed: [F.wsw7] })
    ])
    assert.deepStrictEqual(Object.keys(bot.state.warnings), [identityKey(F.svw42)])
    assert.deepStrictEqual(bot.state.missingFromDigest, [F.svw43])
    assert.equal(bot.state.needsDigest, false)
    assert.equal(bot.state.digest.digest.feed_health, 7)
    assert.equal(bot.state.digest.receivedAt, F.t0)
  })

  it("a digest refreshes a held warning's expiry from its absolute entry", () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, expiresMinutes: F.t0Minutes + 45 }), F.t0)
    bot.apply(F.digest({ seq: 2, nowMinutes: F.t0Minutes + 10, entries: [[F.svw42, 50]] }), F.t0)
    assert.equal(held(bot.state, F.svw42).warning.expires_min, F.t0Minutes + 60)
  })

  it('a warning that arrives after the digest clears its missing entry', () => {
    const bot = new Bot()
    bot.apply(F.digest({ seq: 1, entries: [[F.svw43, 20]] }), F.t0)
    assert.deepStrictEqual(bot.state.missingFromDigest, [F.svw43])
    bot.apply(F.warning({ seq: 2, identity: F.svw43 }), F.t0)
    assert.deepStrictEqual(bot.state.missingFromDigest, [])
  })

  it('feed staleness follows the four-hour threshold, and 255 is never received', () => {
    const bot = new Bot()
    bot.apply(F.digest({ seq: 1, feedHealth: 60, entries: [] }), F.t0)
    assert.equal(WeatherStoredDigest.isFeedStale(bot.state.digest), false)
    assert.deepStrictEqual(WeatherStoredDigest.feed(bot.state.digest), { kind: 'recent', minutes: 240 })
    bot.apply(F.digest({ seq: 2, feedHealth: 61, entries: [] }), F.t0)
    assert.equal(WeatherStoredDigest.isFeedStale(bot.state.digest), true)
    assert.deepStrictEqual(WeatherStoredDigest.feed(bot.state.digest), { kind: 'quiet', minutes: 244 })
    bot.apply(F.digest({ seq: 3, feedHealth: 255, entries: [] }), F.t0)
    assert.equal(WeatherStoredDigest.isFeedStale(bot.state.digest), true)
    assert.deepStrictEqual(WeatherStoredDigest.feed(bot.state.digest), { kind: 'neverReceived' })
  })

  it('a list removes an omitted warning received over two minutes before it was built, not one received since', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, identity: F.svw42 }), F.t0 - minutes(3))
    bot.apply(F.warning({ seq: 2, identity: F.svw43 }), F.t0 - minutes(1))
    const changes = bot.apply(F.digest({ seq: 3, entries: [] }), F.t0)
    assert.deepStrictEqual(changes, [
      WeatherStateChange.digestApplied({ missing: [], removed: [F.svw42] })
    ])
    assert.deepStrictEqual(Object.keys(bot.state.warnings), [identityKey(F.svw43)])
  })

  it('a full list says nothing about a warning expiring after its last entry', () => {
    const earlier = F.t0 - 3_600_000
    const late = { event: 3, office: 35, etn: 900 }
    const soon = { event: 3, office: 35, etn: 901 }
    // Twenty-five heat advisories expiring 61 to 85 minutes after the list, soonest first.
    const entries = Array.from({ length: MeshWXWire.maxDigestEntries }, (_, index) => [
      { event: 14, office: 35, etn: index + 1 }, 60 + index + 1
    ])

    const makeHeld = () => {
      const bot = new Bot()
      bot.apply(F.warning({ seq: 1, identity: late, expiresMinutes: F.t0Minutes + 600 }), earlier)
      bot.apply(F.warning({ seq: 2, identity: soon, expiresMinutes: F.t0Minutes + 30 }), earlier)
      return bot
    }

    const full = makeHeld()
    const changes = full.apply(F.digest({ seq: 3, entries }), F.t0)
    // The one expiring before the last entry would have been listed; the later one may have been cut.
    assert.deepStrictEqual(changes, [
      WeatherStateChange.digestApplied({ missing: entries.map(([identity]) => identity), removed: [soon] })
    ])
    assert.ok(held(full.state, late) != null)

    const short = makeHeld()
    short.apply(F.digest({ seq: 3, entries: entries.slice(0, -1) }), F.t0)
    assert.deepStrictEqual(short.state.warnings, {}, 'a list with room to spare speaks for everything')
  })

  // MARK: - Observations and forecasts

  it('observations merge per station and an older batch never rolls one back', () => {
    const bot = new Bot()
    bot.apply(
      F.observations({ seq: 1, timestampMinutes: F.t0Minutes, stations: [[202, 88], [860, 84]] }),
      F.t0
    )
    // A single-station answer for KAUS from a newer batch.
    let changes = bot.apply(
      F.observations({ seq: 2, timestampMinutes: F.t0Minutes + 30, stations: [[202, 90]] }),
      F.t0
    )
    assert.deepStrictEqual(changes, [WeatherStateChange.observationsStored({ stations: [202] })])
    assert.equal(bot.state.observations['202'].observation.temp_f, 90)
    assert.equal(bot.state.observations['860'].observation.temp_f, 84)

    // A cached re-send of the first batch, older than what is held for 202.
    changes = bot.apply(
      F.observations({ seq: 3, timestampMinutes: F.t0Minutes, stations: [[202, 88], [976, 70]] }),
      F.t0
    )
    assert.deepStrictEqual(changes, [WeatherStateChange.observationsStored({ stations: [976] })])
    assert.equal(bot.state.observations['202'].observation.temp_f, 90)
    assert.equal(WeatherBotState.latestObservationMinutes(bot.state), F.t0Minutes + 30)
  })

  it('observation staleness is two hours from the batch time', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 1, timestampMinutes: F.t0Minutes, stations: [[202, 88]] }), F.t0)
    const stored = bot.state.observations['202']
    assert.equal(WeatherStoredObservation.isStale(stored, { at: F.t0 + minutes(119) }), false)
    assert.equal(WeatherStoredObservation.isStale(stored, { at: F.t0 + minutes(121) }), true)
  })

  // Spec §6.1: `ts` is the newest station's time and the rest say how far behind they are, so a
  // reading is stored at the time its own station measured it.
  it('each station is stored at its own report time, and the batch time stays on the row', () => {
    const bot = new Bot()
    bot.apply(
      F.observations({
        seq: 1,
        timestampMinutes: F.t0Minutes,
        stations: [[202, 88], [860, 84], [976, 70]],
        ages: [0, 20, 110]
      }),
      F.t0
    )
    assert.equal(bot.state.observations['202'].timestampMinutes, F.t0Minutes, 'the newest reads the batch time')
    assert.equal(bot.state.observations['860'].timestampMinutes, F.t0Minutes - 20)
    assert.equal(bot.state.observations['976'].timestampMinutes, F.t0Minutes - 110)
    assert.equal(
      WeatherStoredObservation.observedAt(bot.state.observations['976']),
      dateFromUnixMinutes(F.t0Minutes - 110)
    )
    // The batch time is not lost with them: it is what says these three arrived in one scheduled
    // broadcast, which is what the bot's area is read from.
    assert.ok(Object.values(bot.state.observations).every((held) => held.lastBatchMinutes === F.t0Minutes))
    assert.equal(WeatherBotState.latestObservationMinutes(bot.state), F.t0Minutes)

    // Without the ages a batch states only its `ts`, and every station in it still reads that.
    const old = new Bot()
    old.apply(
      F.observations({ seq: 1, timestampMinutes: F.t0Minutes, stations: [[202, 88], [860, 84]] }),
      F.t0
    )
    assert.ok(Object.values(old.state.observations).every((h) => h.timestampMinutes === F.t0Minutes))
    assert.equal(old.state.observations['860'].observation.age_min, null)
  })

  // A station two hours behind its batch is stale two hours before the batch is.
  it('a station far behind its batch goes stale ahead of the rest of it', () => {
    const bot = new Bot()
    bot.apply(
      F.observations({
        seq: 1, timestampMinutes: F.t0Minutes, stations: [[202, 88], [860, 84]], ages: [0, 120]
      }),
      F.t0
    )
    const newest = bot.state.observations['202']
    const behind = bot.state.observations['860']
    // Two hours is the threshold, measured from each station's own reading.
    assert.equal(WeatherStoredObservation.isStale(behind, { at: F.t0 }), false)
    assert.equal(WeatherStoredObservation.isStale(behind, { at: F.t0 + minutes(2) }), true)
    assert.equal(WeatherStoredObservation.isStale(newest, { at: F.t0 + minutes(119) }), false)
    assert.equal(WeatherStoredObservation.isStale(newest, { at: F.t0 + minutes(121) }), true)
  })

  // Newest wins per station, and "newest" is the station's own time.
  it('a newer batch carrying an older report for one station does not roll it back', () => {
    const bot = new Bot()
    bot.apply(
      F.observations({
        seq: 1, timestampMinutes: F.t0Minutes, stations: [[202, 88], [860, 84]], ages: [0, 0]
      }),
      F.t0
    )
    // The next hour: KAUS filed again, KGTU's newest METAR is now 90 minutes behind the batch —
    // older than the copy already held.
    const changes = bot.apply(
      F.observations({
        seq: 2, timestampMinutes: F.t0Minutes + 60, stations: [[202, 90], [860, 70]], ages: [0, 90]
      }),
      F.t0 + minutes(60)
    )
    assert.deepStrictEqual(changes, [WeatherStateChange.observationsStored({ stations: [202] })])
    assert.equal(bot.state.observations['202'].observation.temp_f, 90)
    assert.equal(bot.state.observations['202'].timestampMinutes, F.t0Minutes + 60)
    assert.equal(bot.state.observations['860'].observation.temp_f, 84, 'the reading held is the newer one')
    assert.equal(bot.state.observations['860'].timestampMinutes, F.t0Minutes)
    assert.equal(
      bot.state.observations['860'].lastBatchMinutes, F.t0Minutes + 60, 'it was in that batch all the same'
    )
  })

  it('forecasts are keyed by point and an older issue is ignored', () => {
    const bot = new Bot()
    bot.apply(F.forecast({ seq: 1, point: 102, issuedMinutes: F.t0Minutes }), F.t0)
    bot.apply(F.forecast({ seq: 2, point: 0xffff, issuedMinutes: F.t0Minutes }), F.t0)
    const changes = bot.apply(F.forecast({ seq: 3, point: 102, issuedMinutes: F.t0Minutes - 60 }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.forecastIgnoredOlder({ point: 102 })])
    assert.equal(bot.state.forecasts['102'].forecast.issued_min, F.t0Minutes)
    // Revision 10: a point the bot chose for itself is not in `forecasts` at all. It knows which
    // weather, never which place, so it is parked in the one slot for answers to questions
    // nobody on this phone asked — never under an index no place has.
    assert.deepStrictEqual(Object.keys(bot.state.forecasts), ['102'])
    assert.equal(
      WeatherStoredForecast.isUnbundledPoint(bot.state.unbundledForecasts['?'].forecast), true
    )
    assert.equal(bot.state.unbundledForecasts['?'].requestedHere, false)
    assert.equal(Object.keys(bot.state.unbundledForecasts).length, 1)
  })

  // Design §2: twelve slots, oldest dropped. Twelve is a pocketful of places asked about by
  // coordinate; past that the oldest question is the one nobody has come back to.
  it('bot-chosen forecasts are capped at twelve, oldest dropped', () => {
    const held = {}
    for (let index = 0; index < 15; index += 1) {
      held[`3${index}.000,-97.000`] = { forecast: {}, receivedAt: F.t0 + index * 1000, requestedHere: true }
    }
    const kept = WeatherStateReducer.retainUnbundledForecasts(held)
    assert.equal(Object.keys(kept).length, 12)
    assert.equal(kept['30.000,-97.000'], undefined, 'the three oldest went')
    assert.equal(kept['32.000,-97.000'], undefined)
    assert.ok(kept['33.000,-97.000'] != null)
    assert.ok(kept['314.000,-97.000'] != null)
  })

  // MARK: - Text

  it('text chunks assemble by group in index order and report the missing part', () => {
    const bot = new Bot()
    bot.apply(F.text({ seq: 23, group: 23, index: 0, total: 3, text: 'SEVERE ' }), F.t0)
    const changes = bot.apply(
      F.text({ seq: 25, group: 23, index: 2, total: 3, text: 'indicated.' }), F.t0
    )
    assert.ok(changes.some((change) => change.kind === 'textChunkStored'
      && change.group === 23 && change.index === 2 && change.isComplete === false))
    const assembly = bot.state.texts['23']
    assert.deepStrictEqual(WeatherTextAssembly.missingIndexes(assembly), [1])
    assert.deepStrictEqual(WeatherTextAssembly.orderedChunks(assembly), ['SEVERE ', null, 'indicated.'])
    assert.equal(WeatherTextAssembly.isComplete(assembly), false)

    // The re-sent reply carries the original group byte and fills the hole.
    bot.apply(F.text({ seq: 30, group: 23, index: 1, total: 3, text: 'THUNDERSTORM ' }), F.t0)
    const complete = bot.state.texts['23']
    assert.equal(WeatherTextAssembly.isComplete(complete), true)
    assert.equal(
      WeatherTextAssembly.orderedChunks(complete).filter((chunk) => chunk != null).join(''),
      'SEVERE THUNDERSTORM indicated.'
    )
  })

  it('a new subject under a reused group byte starts a fresh assembly', () => {
    const bot = new Bot()
    bot.apply(F.text({ seq: 1, subject: 0, group: 9, index: 0, total: 2, text: 'old' }), F.t0)
    bot.apply(F.text({ seq: 2, subject: 1, group: 9, index: 0, total: 1, text: 'new' }), F.t0)
    const assembly = bot.state.texts['9']
    assert.equal(assembly.subject, 1)
    assert.equal(WeatherTextAssembly.isComplete(assembly), true)
    assert.deepStrictEqual(WeatherTextAssembly.orderedChunks(assembly), ['new'])
  })

  // MARK: - Where the data came from (spec §2.2, revision 7)

  it("the source rides from each message's header into the record the screen reads", () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, source: 1 }), F.t0)
    bot.apply(F.observations({ seq: 2, stations: [[202, 88]], source: 2 }), F.t0)
    bot.apply(F.forecast({ seq: 3, source: 3 }), F.t0)
    assert.equal(held(bot.state, F.svw42).source, 1)
    assert.equal(bot.state.observations['202'].source, 2)
    assert.equal(bot.state.forecasts['102'].source, 3)
  })

  // A bot older than revision 7 states nothing, and nothing is what the record holds: unstated
  // is not a fourth source, and no screen may read it as one.
  it('a bot that states no source leaves every record unstated', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1 }), F.t0)
    bot.apply(F.observations({ seq: 2, stations: [[202, 88]] }), F.t0)
    bot.apply(F.forecast({ seq: 3 }), F.t0)
    bot.apply(F.text({ seq: 4, group: 4, index: 0, total: 1, text: 'x' }), F.t0)
    assert.equal(held(bot.state, F.svw42).source, 0)
    assert.equal(bot.state.observations['202'].source, 0)
    assert.equal(bot.state.forecasts['102'].source, 0)
    assert.equal(bot.state.texts['4'].source, 0)
    assert.equal(bot.state.texts['4'].wasCut, false)
  })

  // A Cancel's nibble is its reason (spec §4), so nothing about it is a source — and the reason
  // still decodes as it always did whatever the value.
  it('a cancel whose reason fills the source bits is still only a reason', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1 }), F.t0)
    // Reason 12 has bits 3-2 set: read as a source it would say "mixed".
    const message = F.cancel({ seq: 2, reason: 12 })
    assert.equal(message.source ?? 0, 0)
    const changes = bot.apply(message, F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.warningRemoved(F.svw42, { reason: 12 })])
  })

  it('an assembly takes the source from its chunks and is cut if any chunk says so', () => {
    const bot = new Bot()
    bot.apply(
      F.text({ seq: 7, group: 7, index: 0, total: 3, text: 'SEVERE ', wasCut: true, source: 1 }),
      F.t0
    )
    assert.equal(bot.state.texts['7'].source, 1)
    assert.equal(bot.state.texts['7'].wasCut, true)

    // The cut mark is the reply's, not one chunk's.
    bot.apply(F.text({ seq: 8, group: 7, index: 1, total: 3, text: 'THUNDERSTORM ', source: 1 }), F.t0)
    assert.equal(bot.state.texts['7'].wasCut, true)

    // A chunk that states nothing never erases a source already stated; one that states
    // something disagreeing is the newest word on it.
    bot.apply(F.text({ seq: 9, group: 7, index: 2, total: 3, text: 'indicated.' }), F.t0)
    assert.equal(bot.state.texts['7'].source, 1)
    bot.apply(F.text({ seq: 10, group: 7, index: 2, total: 3, text: 'indicated.', source: 3 }), F.t0)
    assert.equal(bot.state.texts['7'].source, 3)
    assert.equal(WeatherTextAssembly.isComplete(bot.state.texts['7']), true)
  })

  // MARK: - Other types

  it('not-available and unknown types touch nothing but the sequence', () => {
    const bot = new Bot()
    const refusal = F.notAvailable({ seq: 1, letter: 'f', reason: 1 })
    let changes = bot.apply(refusal, F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.notAvailable(refusal)])

    const unknown = { seq: 2, bot: F.botID, type: 12, name: 'unknown', flags: 0 }
    changes = bot.apply(unknown, F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.unknownType({ rawType: 12 })])
    assert.equal(bot.state.lastSeq, 2)
    assert.deepStrictEqual(bot.state.warnings, {})
  })

  // MARK: - Persistence shape

  it('state round-trips through the store', async () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1 }), F.t0)
    bot.apply(F.digest({ seq: 2, entries: [[F.svw42, 45], [F.svw43, 20]] }), F.t0)
    bot.apply(F.observations({ seq: 3, stations: [[202, 88]] }), F.t0)
    bot.apply(F.forecast({ seq: 4 }), F.t0)
    bot.apply(F.text({ seq: 5, group: 5, index: 0, total: 1, text: 'Ünïcode ✓' }), F.t0)

    const storage = new Map()
    const store = new KeyValueWeatherStateStore({
      storage: {
        async get(key) { return storage.get(key) ?? null },
        async set(key, value) { storage.set(key, JSON.parse(JSON.stringify(value))) },
        async delete(key) { storage.delete(key) }
      }
    })
    await store.save({ [String(F.botID)]: bot.state })
    const loaded = await store.load()
    assert.deepStrictEqual(loaded, { [String(F.botID)]: bot.state })
    // PORTING.md rule 4: every value in state survives the JSON round trip unchanged.
    assert.deepStrictEqual(
      JSON.parse(JSON.stringify(weatherStateSnapshot({ [String(F.botID)]: bot.state }))),
      weatherStateSnapshot({ [String(F.botID)]: bot.state })
    )
  })

  it('a missing or unreadable blob loads as empty', async () => {
    let stored = null
    const store = new KeyValueWeatherStateStore({
      storage: {
        async get() { return stored },
        async set(key, value) { stored = value },
        async delete() { stored = null }
      }
    })
    assert.deepStrictEqual(await store.load(), {})
    stored = 'not json'
    assert.deepStrictEqual(await store.load(), {})
    stored = { version: 99, bots: [] }
    assert.deepStrictEqual(await store.load(), {})
  })
})

// Spec §7A: the bot's own statement of what it carries, which has no time of its own.
describe('WeatherStateReducer coverage', () => {
  it('a statement is stored with when it arrived', () => {
    const bot = new Bot()
    const changes = bot.apply(F.coverage({ seq: 1 }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.coverageStored])
    assert.equal(bot.state.coverage.coverage.radius_km, F.austinCoverage.radius_km)
    assert.equal(bot.state.coverage.receivedAt, F.t0)
  })

  it('the newest statement replaces the one held', () => {
    const bot = new Bot()
    bot.apply(F.coverage({ seq: 1 }), F.t0)

    const widened = { ...F.austinCoverage, radius_km: 200 }
    const changes = bot.apply(F.coverage({ seq: 2, coverage: widened }), F.t0 + 3 * 3_600_000)
    assert.deepStrictEqual(changes, [WeatherStateChange.coverageStored])
    assert.equal(bot.state.coverage.coverage.radius_km, 200)
    assert.equal(bot.state.coverage.receivedAt, F.t0 + 3 * 3_600_000)
  })

  // A late resend arriving behind a newer statement was sent first; the held one stands.
  it('a statement arriving out of order leaves the newer one alone', () => {
    const bot = new Bot()
    bot.apply(F.coverage({ seq: 40 }), F.t0)

    const older = { ...F.austinCoverage, radius_km: 80 }
    const changes = bot.apply(F.coverage({ seq: 20, coverage: older }), F.t0 + 10_000)
    assert.ok(changes.some((change) => change.kind === 'outOfOrder' && change.seq === 20))
    assert.ok(changes.some((change) => change.kind === 'coverageIgnoredOlder'))
    assert.equal(bot.state.coverage.coverage.radius_km, 120)
  })

  // The same bytes twice — the bot's resend of an unechoed packet — change nothing.
  it('a copy of a statement is a duplicate', () => {
    const bot = new Bot()
    bot.apply(F.coverage({ seq: 1 }), F.t0)
    const before = bot.state
    const changes = bot.apply(F.coverage({ seq: 1 }), F.t0 + 9000)
    assert.deepStrictEqual(changes, [WeatherStateChange.duplicate({ seq: 1 })])
    assert.deepStrictEqual(bot.state, before)
  })
})

describe('WeatherStateReducer ordering', () => {
  const warningWithAreas = ({ seq, identity, areas, polygon = null }) => F.warning({
    seq, identity, expiresMinutes: F.t0Minutes + 120, areas, polygon
  })

  it('an empty list built before a new warning arrived does not remove it, however late it comes', () => {
    const bot = new Bot()
    // 23:00 list, empty. 23:02 tornado warning. 23:04 a copy of the 23:00 list arrives late.
    bot.apply(F.digest({ seq: 1, nowMinutes: F.t0Minutes, entries: [] }), F.t0)
    bot.apply(F.warning({ seq: 2, identity: F.svw43 }), F.t0 + minutes(2))
    const changes = bot.apply(F.digest({ seq: 3, nowMinutes: F.t0Minutes, entries: [] }), F.t0 + minutes(4))
    assert.deepStrictEqual(changes, [WeatherStateChange.digestApplied({ missing: [], removed: [] })])
    assert.ok(held(bot.state, F.svw43) != null)
  })

  it('a list built before the one held changes nothing', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, identity: F.svw42 }), F.t0 - minutes(60))
    bot.apply(F.digest({ seq: 2, nowMinutes: F.t0Minutes, entries: [[F.svw42, 45]] }), F.t0)
    // An eight-hour-old list drained late from the radio's queue.
    const changes = bot.apply(
      F.digest({ seq: 3, nowMinutes: F.t0Minutes - 480, entries: [] }), F.t0 + 60_000
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.digestIgnoredOlder({ builtMinutes: F.t0Minutes - 480 })
    ])
    assert.ok(held(bot.state, F.svw42) != null)
    assert.equal(bot.state.digest.digest.now_min, F.t0Minutes)
  })

  it('a list may extend but not shorten a warning that arrived after it was built', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, expiresMinutes: F.t0Minutes + 90 }), F.t0)
    // Built three minutes before that warning arrived, with an earlier expiry.
    bot.apply(
      F.digest({ seq: 2, nowMinutes: F.t0Minutes - 3, entries: [[F.svw42, 30]] }), F.t0 + 60_000
    )
    assert.equal(held(bot.state, F.svw42).warning.expires_min, F.t0Minutes + 90)
  })

  it('a list built within two minutes of a gap does not clear it, one built later does', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 1, stations: [[202, 88]] }), F.t0)
    // seq 2 is lost; seq 3 reveals the gap at t0.
    bot.apply(F.observations({ seq: 3, stations: [[202, 88]] }), F.t0)
    bot.apply(F.digest({ seq: 4, nowMinutes: F.t0Minutes + 1, entries: [] }), F.t0 + 60_000)
    assert.equal(
      bot.state.needsDigest, true,
      'a minute after the gap, the two clocks could still disagree about the order'
    )
    bot.apply(F.digest({ seq: 5, nowMinutes: F.t0Minutes + 3, entries: [] }), F.t0 + 180_000)
    assert.equal(bot.state.needsDigest, false)
  })

  it('a gap seen after the list was built survives a late copy of it', () => {
    const bot = new Bot()
    bot.apply(F.digest({ seq: 1, nowMinutes: F.t0Minutes, entries: [] }), F.t0)
    // seq 2 is lost; seq 3 reveals the gap three minutes later.
    bot.apply(F.observations({ seq: 3, stations: [[202, 88]] }), F.t0 + minutes(3))
    assert.equal(bot.state.needsDigest, true)
    bot.apply(F.digest({ seq: 4, nowMinutes: F.t0Minutes, entries: [] }), F.t0 + minutes(4))
    assert.equal(
      bot.state.needsDigest, true, 'the re-sent list was built before the gap and cannot vouch for it'
    )
    // A list built well after the gap clears it.
    bot.apply(F.digest({ seq: 5, nowMinutes: F.t0Minutes + 180, entries: [] }), F.t0 + minutes(180))
    assert.equal(bot.state.needsDigest, false)
  })

  it('an upgrade leaves a marker until an overlapping replacement arrives', () => {
    const bot = new Bot()
    const travis = [{ state: 42, county: true, start: 453, run: 1 }]
    bot.apply(warningWithAreas({ seq: 1, identity: F.svw42, areas: travis }), F.t0)
    bot.apply(F.cancel({ seq: 2, identity: F.svw42, reason: 2 }), F.t0)
    assert.deepStrictEqual(bot.state.warnings, {})
    assert.ok(bot.state.pendingUpgrades[identityKey(F.svw42)] != null)

    const tornado = { event: 1, office: 35, etn: 9 }
    bot.apply(warningWithAreas({ seq: 3, identity: tornado, areas: travis }), F.t0)
    assert.deepStrictEqual(bot.state.pendingUpgrades, {})
  })

  it('a warning elsewhere from the same office does not clear an upgrade marker', () => {
    const bot = new Bot()
    const travis = [{ state: 42, county: true, start: 453, run: 1 }]
    const llano = [{ state: 42, county: true, start: 299, run: 1 }]
    bot.apply(warningWithAreas({ seq: 1, identity: F.svw42, areas: travis }), F.t0)
    bot.apply(F.cancel({ seq: 2, identity: F.svw42, reason: 2 }), F.t0)
    bot.apply(warningWithAreas({ seq: 3, identity: F.svw43, areas: llano }), F.t0)
    assert.ok(bot.state.pendingUpgrades[identityKey(F.svw42)] != null)
  })

  it('a cancel that is not an upgrade leaves no marker', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1 }), F.t0)
    bot.apply(F.cancel({ seq: 2, reason: 0 }), F.t0)
    assert.deepStrictEqual(bot.state.pendingUpgrades, {})
  })

  it('a late copy of a recent message is a duplicate, not a gap', () => {
    const bot = new Bot()
    for (const seq of [5, 6, 7]) {
      bot.apply(F.observations({ seq, stations: [[202, 88]] }), F.t0)
    }
    const changes = bot.apply(F.observations({ seq: 6, stations: [[202, 88]] }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.duplicate({ seq: 6 })])
    assert.equal(bot.state.lastSeq, 7)
    assert.equal(bot.state.needsDigest, false)
  })

  it('a late warning for an identity held from a newer message is not applied, and asks for a list', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 98, windMph: 70 }), F.t0)
    bot.apply(F.observations({ seq: 100, stations: [[202, 88]] }), F.t0)
    const changes = bot.apply(F.warning({ seq: 97, windMph: 60 }), F.t0)
    assert.deepStrictEqual(changes, [WeatherStateChange.outOfOrder({ seq: 97 })])
    assert.equal(held(bot.state, F.svw42).warning.wind_mph, 70)
    assert.equal(bot.state.needsDigest, true)
    assert.equal(bot.state.lastSeq, 100)
  })

  it("the bot's late resend of an update replaces the older copy it updates", () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 95, windMph: 60 }), F.t0)
    // 96, the update, is missed; 97 arrives; 96's resend follows nine seconds later.
    bot.apply(F.observations({ seq: 97, stations: [[202, 88]] }), F.t0)
    const changes = bot.apply(F.warning({ seq: 96, windMph: 70 }), F.t0 + 9000)
    assert.deepStrictEqual(changes, [
      WeatherStateChange.outOfOrder({ seq: 96 }),
      WeatherStateChange.warningStored(F.svw42, { replacedExisting: true })
    ])
    assert.equal(held(bot.state, F.svw42).warning.wind_mph, 70)
    assert.equal(held(bot.state, F.svw42).seq, 96)
    assert.equal(bot.state.lastSeq, 97)
  })

  it('out of order across the wrap from 255 to 0 is still out of order, not a restart', () => {
    const bot = new Bot()
    for (const seq of [255, 0, 1]) {
      bot.apply(F.observations({ seq, stations: [[202, 88]] }), F.t0)
    }
    const changes = bot.apply(F.warning({ seq: 254, identity: F.svw43 }), F.t0)
    assert.deepStrictEqual(changes, [
      WeatherStateChange.outOfOrder({ seq: 254 }),
      WeatherStateChange.warningStored(F.svw43, { replacedExisting: false })
    ])
    assert.equal(bot.state.lastSeq, 1)
  })

  it('a late warning for an identity not held is stored, since it is the only copy', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 100, stations: [[202, 88]] }), F.t0)
    const changes = bot.apply(F.warning({ seq: 96, identity: F.svw43 }), F.t0)
    assert.deepStrictEqual(changes, [
      WeatherStateChange.outOfOrder({ seq: 96 }),
      WeatherStateChange.warningStored(F.svw43, { replacedExisting: false })
    ])
    assert.equal(bot.state.lastSeq, 100)
  })

  it('a late warning for an identity cancelled in the last hour is not brought back', () => {
    const bot = new Bot()
    bot.apply(F.cancel({ seq: 100, identity: F.svw42 }), F.t0)
    const changes = bot.apply(F.warning({ seq: 98, identity: F.svw42 }), F.t0 + 10_000)
    assert.deepStrictEqual(changes, [WeatherStateChange.outOfOrder({ seq: 98 })])
    assert.deepStrictEqual(bot.state.warnings, {})
    assert.equal(bot.state.recentCancels[identityKey(F.svw42)], F.t0)

    bot.apply(F.observations({ seq: 101, stations: [[202, 88]] }), F.t0 + minutes(61))
    assert.deepStrictEqual(bot.state.recentCancels, {}, 'remembered for an hour')
  })

  it('a late cancel still ends its warning', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 10 }), F.t0)
    bot.apply(F.observations({ seq: 12, stations: [[202, 88]] }), F.t0)
    const changes = bot.apply(F.cancel({ seq: 11 }), F.t0)
    assert.deepStrictEqual(changes, [
      WeatherStateChange.outOfOrder({ seq: 11 }),
      WeatherStateChange.warningRemoved(F.svw42, { reason: 1 })
    ])
    assert.deepStrictEqual(bot.state.warnings, {})
  })

  it('a late upgrade cancel leaves no marker when its replacement is already held', () => {
    const bot = new Bot()
    const travis = [{ state: 42, county: true, start: 453, run: 1 }]
    const tornado = { event: 1, office: 35, etn: 9 }
    bot.apply(warningWithAreas({ seq: 10, identity: F.svw42, areas: travis }), F.t0)
    bot.apply(warningWithAreas({ seq: 12, identity: tornado, areas: travis }), F.t0)
    bot.apply(F.cancel({ seq: 11, identity: F.svw42, reason: 2 }), F.t0)
    assert.deepStrictEqual(Object.keys(bot.state.warnings), [identityKey(tornado)])
    assert.deepStrictEqual(bot.state.pendingUpgrades, {})
  })

  it('a late list goes through the build-time check like any other', () => {
    const bot = new Bot()
    bot.apply(F.digest({ seq: 10, entries: [] }), F.t0)
    bot.apply(F.observations({ seq: 12, stations: [[202, 88]] }), F.t0)
    assert.deepStrictEqual(
      bot.apply(F.digest({ seq: 11, nowMinutes: F.t0Minutes - 30, entries: [] }), F.t0),
      [
        WeatherStateChange.outOfOrder({ seq: 11 }),
        WeatherStateChange.digestIgnoredOlder({ builtMinutes: F.t0Minutes - 30 })
      ]
    )
    assert.deepStrictEqual(
      bot.apply(F.digest({ seq: 9, nowMinutes: F.t0Minutes + 5, entries: [[F.svw43, 30]] }), F.t0),
      [
        WeatherStateChange.outOfOrder({ seq: 9 }),
        WeatherStateChange.digestApplied({ missing: [F.svw43], removed: [] })
      ]
    )
  })

  it('a seq far behind is a restart: a new stream, applied normally, and a gap', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 100, stations: [[202, 88]] }), F.t0)
    const changes = bot.apply(F.warning({ seq: 50 }), F.t0 + 60_000)
    assert.deepStrictEqual(changes, [
      WeatherStateChange.sequenceRestart({ last: 100, received: 50 }),
      WeatherStateChange.warningStored(F.svw42, { replacedExisting: false })
    ])
    assert.equal(bot.state.lastSeq, 50)
    assert.equal(bot.state.needsDigest, true)
    assert.deepStrictEqual(bot.state.recentMessages.map((seen) => seen.seq), [50])
    // The new stream goes on from there without another gap.
    assert.deepStrictEqual(
      bot.apply(F.observations({ seq: 51, stations: [[202, 88]] }), F.t0 + 62_000),
      [WeatherStateChange.observationsStored({ stations: [202] })]
    )
  })

  it('reordering reaches 32 places back, and 33 is a restart', () => {
    const after100 = (seq) => {
      const bot = new Bot()
      bot.apply(F.observations({ seq: 100, stations: [[202, 88]] }), F.t0)
      return bot.apply(
        F.observations({ seq, timestampMinutes: F.t0Minutes - 1, stations: [[860, 80]] }), F.t0
      )[0]
    }
    assert.deepStrictEqual(after100(99), WeatherStateChange.outOfOrder({ seq: 99 }))
    assert.deepStrictEqual(after100(68), WeatherStateChange.outOfOrder({ seq: 68 }))
    assert.deepStrictEqual(after100(67), WeatherStateChange.sequenceRestart({ last: 100, received: 67 }))
  })

  it('a new message under the newest seq after a restart is not taken for a copy', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 40 }), F.t0)
    // The bot restarts and its counter lands on 40 again.
    const changes = bot.apply(F.cancel({ seq: 40 }), F.t0 + 120_000)
    assert.deepStrictEqual(changes, [
      WeatherStateChange.sequenceRestart({ last: 40, received: 40 }),
      WeatherStateChange.warningRemoved(F.svw42, { reason: 1 })
    ])
    assert.deepStrictEqual(bot.state.warnings, {})
    // The bot's resend of that cancel is a copy.
    assert.deepStrictEqual(
      bot.apply(F.cancel({ seq: 40 }), F.t0 + 129_000),
      [WeatherStateChange.duplicate({ seq: 40 })]
    )
  })

  it('after six hours of silence a repeated seq is new, and the silence is a gap', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 10, stations: [[202, 88]] }), F.t0)
    const changes = bot.apply(
      F.observations({ seq: 10, timestampMinutes: F.t0Minutes + 420, stations: [[202, 80]] }),
      F.t0 + minutes(420)
    )
    assert.deepStrictEqual(changes[0], WeatherStateChange.sequenceGap({ expected: 11, received: 10 }))
    assert.equal(bot.state.observations['202'].observation.temp_f, 80)
    assert.equal(bot.state.needsDigest, true)
  })

  it("a list's age is its build time, not its arrival", () => {
    const bot = new Bot()
    bot.apply(F.digest({ seq: 1, nowMinutes: F.t0Minutes - 480, entries: [] }), F.t0)
    assert.equal(WeatherStoredDigest.builtAt(bot.state.digest), dateFromUnixMinutes(F.t0Minutes - 480))
    assert.equal(bot.state.digest.receivedAt, F.t0)
  })

  it('observations remember how many stations their batch carried', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 1, stations: [[202, 88], [860, 84]] }), F.t0)
    bot.apply(
      F.observations({ seq: 2, timestampMinutes: F.t0Minutes + 5, stations: [[976, 80]] }), F.t0
    )
    assert.equal(bot.state.observations['202'].batchSize, 2)
    assert.equal(bot.state.observations['976'].batchSize, 1)
  })

  // A single-station answer is somebody's `>o KAUS`, broadcast to everyone. The newer reading is
  // the one to show, but it says nothing about the bot's area.
  it('a single-station answer replaces the reading but not the batch it was in', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 1, stations: [[202, 88], [860, 84]] }), F.t0)
    assert.equal(bot.state.observations['202'].lastBatchMinutes, F.t0Minutes)

    bot.apply(
      F.observations({ seq: 2, timestampMinutes: F.t0Minutes + 5, stations: [[202, 90]] }), F.t0
    )
    assert.equal(bot.state.observations['202'].observation.temp_f, 90, 'the newer reading is what is shown')
    assert.equal(bot.state.observations['202'].batchSize, 1, 'and it came in a batch of one')
    assert.equal(
      bot.state.observations['202'].lastBatchMinutes, F.t0Minutes,
      'it was in the scheduled batch, and still is'
    )

    // A station nothing but a single answer has ever named has no batch behind it.
    bot.apply(
      F.observations({ seq: 3, timestampMinutes: F.t0Minutes + 6, stations: [[976, 70]] }), F.t0
    )
    assert.equal(bot.state.observations['976'].lastBatchMinutes, null)

    // A scheduled batch drained late, older than the single answer, is still evidence.
    bot.apply(
      F.observations({ seq: 4, timestampMinutes: F.t0Minutes + 2, stations: [[976, 71], [860, 85]] }),
      F.t0
    )
    assert.equal(bot.state.observations['976'].observation.temp_f, 70, 'the newer single reading stands')
    assert.equal(bot.state.observations['976'].lastBatchMinutes, F.t0Minutes + 2)
  })

  // A state blob written before the batch was recorded separately: a reading that came in a batch
  // is its own evidence, which is exactly what `batchSize` alone used to say.
  it('a reading from a blob without the batch record keeps its batch', () => {
    const bot = new Bot()
    bot.apply(F.observations({ seq: 1, stations: [[202, 88], [860, 84]] }), F.t0)
    const json = JSON.parse(JSON.stringify(bot.state))
    for (const record of Object.values(json.observations)) delete record.lastBatchMinutes

    const legacy = WeatherBotState.decode(json)
    assert.equal(legacy.observations['202'].lastBatchMinutes, F.t0Minutes)
    assert.equal(legacy.observations['202'].batchSize, 2)
  })

  it('a state blob written before the new fields still loads', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1, source: 2 }), F.t0)
    bot.apply(F.observations({ seq: 2, stations: [[202, 88]], source: 2 }), F.t0)
    bot.apply(F.forecast({ seq: 3, source: 2 }), F.t0)
    bot.apply(F.text({ seq: 4, group: 4, index: 0, total: 1, text: 'x', wasCut: true, source: 2 }), F.t0)

    const json = JSON.parse(JSON.stringify(bot.state))
    for (const key of ['recentMessages', 'gapDetectedAt', 'pendingUpgrades', 'recentCancels']) {
      delete json[key]
    }
    // Before fingerprints the window was bare seq values.
    json.recentSeqs = [3, 4]
    const strip = (collection, field) => {
      for (const record of Object.values(json[collection] ?? {})) delete record[field]
    }
    strip('observations', 'batchSize')
    strip('forecasts', 'requestedHere')
    strip('texts', 'request')
    // Revision 7 (spec §2.2, §8.1): a blob written before the app could read either of these
    // decodes as "the radio didn't say", not as a failure to load.
    for (const collection of ['warnings', 'observations', 'forecasts', 'texts']) {
      strip(collection, 'source')
    }
    strip('texts', 'wasCut')

    const legacy = WeatherBotState.decode(json)
    assert.equal(Object.keys(legacy.warnings).length, 1)
    assert.deepStrictEqual(legacy.recentMessages, [
      { seq: 3, fingerprint: null }, { seq: 4, fingerprint: null }
    ])
    assert.deepStrictEqual(legacy.recentCancels, {})
    assert.equal(legacy.observations['202'].batchSize, 1)
    assert.equal(legacy.forecasts['102'].requestedHere, false)
    assert.equal(legacy.texts['4'].request, null)
    assert.equal(held(legacy, F.svw42).source, 0)
    assert.equal(legacy.observations['202'].source, 0)
    assert.equal(legacy.forecasts['102'].source, 0)
    assert.equal(legacy.texts['4'].source, 0)
    assert.equal(legacy.texts['4'].wasCut, false)

    // An entry without a fingerprint matches on seq alone, as it did.
    const reloaded = new Bot()
    reloaded.state = legacy
    assert.deepStrictEqual(
      reloaded.apply(F.observations({ seq: 4, stations: [[202, 70]] }), F.t0),
      [WeatherStateChange.duplicate({ seq: 4 })]
    )
  })
})

// Texts and forecasts get the treatment readings have always had: an age and a ceiling, so the
// answers the channel carries to everybody else cannot pile up for ever (docs/MESHWX_UI.md §12).
describe('Weather retention', () => {
  const cutoff = F.t0 - 48 * 3_600_000

  const reply = ({ group, subject = 6, request = null, hoursAgo }) => {
    const at = F.t0 - hoursAgo * 3_600_000
    return WeatherTextAssembly.make({
      subject, group, total: 1, chunks: { 0: 'text' }, firstReceivedAt: at, lastReceivedAt: at, request
    })
  }

  const stateWithTexts = (texts) => {
    const state = WeatherBotState.make({ botID: F.botID })
    for (const assembly of texts) state.texts[String(assembly.group)] = assembly
    return state
  }

  const forecastRecord = ({ point, hoursAgo, requestedHere = false }) => WeatherStoredForecast.make({
    forecast: {
      point, issued_min: F.t0Minutes - Math.trunc(hoursAgo * 60), first_period: 0, periods: []
    },
    receivedAt: F.t0 - hoursAgo * 3_600_000,
    requestedHere
  })

  const stateWithForecasts = (forecasts) => {
    const state = WeatherBotState.make({ botID: F.botID })
    for (const stored of forecasts) state.forecasts[String(stored.forecast.point)] = stored
    return state
  }

  // MARK: - Texts

  it('a reply the channel carried two days ago goes', () => {
    const state = stateWithTexts([
      reply({ group: 1, subject: 3, hoursAgo: 49 }),
      reply({ group: 2, subject: 3, hoursAgo: 1 })
    ])
    const pruned = WeatherStateReducer.pruneTexts(state, { receivedBefore: cutoff, limit: 24 })
    assert.deepStrictEqual(Object.keys(pruned.texts).sort(), ['2'])
  })

  it('the newest reply on a subject stays however old it is', () => {
    const state = stateWithTexts([reply({ group: 1, subject: 3, hoursAgo: 200 })])
    const pruned = WeatherStateReducer.pruneTexts(state, { receivedBefore: cutoff, limit: 24 })
    assert.deepStrictEqual(Object.keys(pruned.texts).sort(), ['1'])
  })

  it("this phone's own reply and the newest overheard one both stay", () => {
    const state = stateWithTexts([
      reply({ group: 1, subject: 3, request: { kind: 'stormReports', state: 'TX' }, hoursAgo: 60 }),
      reply({ group: 2, subject: 3, hoursAgo: 55 }),
      reply({ group: 3, subject: 3, hoursAgo: 70 })
    ])
    const pruned = WeatherStateReducer.pruneTexts(state, { receivedBefore: cutoff, limit: 24 })
    assert.deepStrictEqual(Object.keys(pruned.texts).map(Number).sort((a, b) => a - b), [1, 2])
  })

  it('the ceiling keeps the newest and never drops what is shown', () => {
    const state = stateWithTexts(
      Array.from({ length: 30 }, (_, index) => reply({ group: index, subject: 8, hoursAgo: index }))
    )
    const pruned = WeatherStateReducer.pruneTexts(state, { receivedBefore: cutoff, limit: 5 })
    assert.equal(Object.keys(pruned.texts).length, 5)
    assert.deepStrictEqual(Object.keys(pruned.texts).map(Number).sort((a, b) => a - b), [0, 1, 2, 3, 4])
  })

  // MARK: - Forecasts

  it('a forecast the channel carried two days ago goes', () => {
    const state = stateWithForecasts([
      forecastRecord({ point: 500, hoursAgo: 49 }), forecastRecord({ point: 501, hoursAgo: 2 })
    ])
    const pruned = WeatherStateReducer.pruneForecasts(state, { receivedBefore: cutoff, limit: 24 })
    assert.deepStrictEqual(Object.keys(pruned.forecasts).map(Number), [501])
  })

  it('the forecast this phone asked for stays however old it is', () => {
    const state = stateWithForecasts([forecastRecord({ point: 103, hoursAgo: 200, requestedHere: true })])
    const pruned = WeatherStateReducer.pruneForecasts(state, { receivedBefore: cutoff, limit: 24 })
    assert.deepStrictEqual(Object.keys(pruned.forecasts).map(Number), [103])
  })

  it("under the ceiling this phone's own forecasts outlast the ones it overheard", () => {
    const state = stateWithForecasts([
      ...Array.from({ length: 10 }, (_, index) => forecastRecord({ point: 600 + index, hoursAgo: index })),
      forecastRecord({ point: 103, hoursAgo: 20, requestedHere: true }),
      forecastRecord({ point: 104, hoursAgo: 30, requestedHere: true })
    ])
    const pruned = WeatherStateReducer.pruneForecasts(state, { receivedBefore: cutoff, limit: 3 })
    assert.deepStrictEqual(
      Object.keys(pruned.forecasts).map(Number).sort((a, b) => a - b), [103, 104, 600]
    )
  })
})

// Spec §7C: the national area sweep — packets assembled by `group` the way Text chunks are, but
// only ever one sweep held, because two sweeps are two pictures of the same country.
describe('WeatherStateReducer area sweep', () => {
  /** The newest sweep held: revision 9's `state.areaSweep`, which is now the head of a list. */
  const held = (bot) => bot.state.areaSweeps[0] ?? null

  it("a packet is stored with the sweep's own build time and the arrival time", () => {
    const bot = new Bot()
    const changes = bot.apply(
      F.areaSweep({ seq: 1, group: 1, index: 0, total: 3, entries: [F.texasSweepEntry] }), F.t0
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.areaSweepStored({ group: 1, index: 0, isComplete: false })
    ])

    const sweep = held(bot)
    assert.equal(sweep.builtMinutes, F.t0Minutes)
    assert.equal(WeatherAreaSweepAssembly.builtAt(sweep), dateFromUnixMinutes(F.t0Minutes))
    assert.equal(sweep.group, 1)
    assert.equal(sweep.total, 3)
    assert.equal(sweep.firstReceivedAt, F.t0)
    assert.equal(sweep.lastReceivedAt, F.t0)
    assert.deepStrictEqual(WeatherAreaSweepAssembly.entries(sweep), [F.texasSweepEntry])
    assert.equal(WeatherAreaSweepAssembly.isComplete(sweep), false)
    assert.deepStrictEqual(WeatherAreaSweepAssembly.missingIndexes(sweep), [1, 2])
    assert.equal(WeatherAreaSweepAssembly.receivedPacketCount(sweep), 1)
  })

  it('packets of one group assemble in index order and complete the sweep', () => {
    const bot = new Bot()
    // Out of order, the way the air delivers them: the last packet arrives before the first.
    for (const [arrival, index, entry] of [[0, 2, F.oklahomaSweepEntry], [2, 0, F.texasSweepEntry]]) {
      bot.apply(
        F.areaSweep({ seq: 1 + index, group: 1, index, total: 3, entries: [entry] }),
        F.t0 + arrival * 1000
      )
    }
    assert.equal(WeatherAreaSweepAssembly.isComplete(held(bot)), false)
    assert.deepStrictEqual(WeatherAreaSweepAssembly.missingIndexes(held(bot)), [1])

    const middle = { event: 9, state: 5, county: false, start: 20, run: 2 }
    const changes = bot.apply(
      F.areaSweep({ seq: 4, group: 1, index: 1, total: 3, entries: [middle] }), F.t0 + 9000
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.areaSweepStored({ group: 1, index: 1, isComplete: true })
    ])
    assert.equal(WeatherAreaSweepAssembly.isComplete(held(bot)), true)
    // Packet order, not arrival order: the bot sends most severe first and the map lays its
    // tints down in that order.
    assert.deepStrictEqual(WeatherAreaSweepAssembly.entries(held(bot)), [
      F.texasSweepEntry, middle, F.oklahomaSweepEntry
    ])
    assert.equal(held(bot).lastReceivedAt, F.t0 + 9000)
    assert.equal(
      held(bot).firstReceivedAt, F.t0,
      'the first packet to *arrive* set this, whatever its index was'
    )
  })

  it('a newer build replaces the held sweep outright rather than merging into it', () => {
    const bot = new Bot()
    bot.apply(F.areaSweep({ seq: 1, group: 1, index: 0, total: 2, entries: [F.texasSweepEntry] }), F.t0)
    bot.apply(F.areaSweep({ seq: 2, group: 1, index: 1, total: 2, entries: [F.oklahomaSweepEntry] }), F.t0)
    assert.equal(WeatherAreaSweepAssembly.isComplete(held(bot)), true)

    // An hour later the bot builds a fresh one.
    const later = { event: 1, state: 42, county: true, start: 453, run: 1 }
    const changes = bot.apply(
      F.areaSweep({
        seq: 3, builtMinutes: F.t0Minutes + 60, group: 3, index: 0, total: 2, entries: [later]
      }),
      F.t0 + 3_600_000
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.areaSweepStored({ group: 3, index: 0, isComplete: false })
    ])
    assert.equal(held(bot).builtMinutes, F.t0Minutes + 60)
    assert.equal(held(bot).group, 3)
    assert.deepStrictEqual(
      WeatherAreaSweepAssembly.entries(held(bot)), [later], 'nothing of the old sweep survives'
    )
    assert.deepStrictEqual(WeatherAreaSweepAssembly.missingIndexes(held(bot)), [1])
    assert.equal(held(bot).firstReceivedAt, F.t0 + 3_600_000)
  })

  it('a sweep built before the one held changes nothing', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, builtMinutes: F.t0Minutes, group: 1, index: 0, total: 1, entries: [F.texasSweepEntry]
      }),
      F.t0
    )
    const before = held(bot)

    // Drained from the radio's queue at connect, an hour after it was built.
    const changes = bot.apply(
      F.areaSweep({
        seq: 200, builtMinutes: F.t0Minutes - 60, group: 200, index: 0, total: 1,
        entries: [F.oklahomaSweepEntry]
      }),
      F.t0 + 60_000
    )
    assert.deepStrictEqual(
      changes[changes.length - 1],
      WeatherStateChange.areaSweepIgnoredOlder({ builtMinutes: F.t0Minutes - 60 })
    )
    assert.deepStrictEqual(held(bot), before)
  })

  it('the same build under a new group is a fresh transmission and starts over', () => {
    const bot = new Bot()
    bot.apply(F.areaSweep({ seq: 1, group: 1, index: 0, total: 2, entries: [F.texasSweepEntry] }), F.t0)
    bot.apply(
      F.areaSweep({ seq: 9, group: 9, index: 1, total: 2, entries: [F.oklahomaSweepEntry] }),
      F.t0 + 30_000
    )
    assert.equal(held(bot).group, 9)
    assert.deepStrictEqual(WeatherAreaSweepAssembly.entries(held(bot)), [F.oklahomaSweepEntry])
    assert.deepStrictEqual(WeatherAreaSweepAssembly.missingIndexes(held(bot)), [0])
  })

  // Both flags are set on every packet of a sweep, so one packet saying so is the sweep saying so.
  it('the cut mark and the advisory scope survive a missing packet', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, group: 1, index: 0, total: 3, entries: [F.texasSweepEntry], wasCut: true,
        includesAdvisories: true, source: 1
      }),
      F.t0
    )
    // Packet 1 never arrives; packet 2 is from an older bot that states no source.
    bot.apply(
      F.areaSweep({
        seq: 3, group: 1, index: 2, total: 3, entries: [F.oklahomaSweepEntry], wasCut: true,
        includesAdvisories: true
      }),
      F.t0 + 4000
    )
    assert.equal(held(bot).wasCut, true)
    assert.equal(held(bot).includesAdvisories, true)
    assert.equal(WeatherAreaSweepAssembly.isComplete(held(bot)), false)
    assert.equal(held(bot).source, 1, 'a silent packet never erases a stated source')
  })

  it('a sweep round-trips through the store', async () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, group: 1, index: 0, total: 2, entries: [F.texasSweepEntry, F.oklahomaSweepEntry],
        wasCut: true, includesAdvisories: true, source: 2
      }),
      F.t0
    )
    const store = new InMemoryWeatherStateStore()
    await store.save({ [String(F.botID)]: bot.state })
    assert.deepStrictEqual(await store.load(), { [String(F.botID)]: bot.state })
  })

  // A state blob written before the app could read a sweep still loads: absent decodes as "no
  // sweep", which is exactly right — nobody had asked for one.
  it('a state blob without the sweep field still decodes', () => {
    const bot = new Bot()
    bot.apply(F.warning({ seq: 1 }), F.t0)

    const json = JSON.parse(JSON.stringify(bot.state))
    assert.deepStrictEqual(json.areaSweeps, [], 'a state with no sweep writes no sweep')
    delete json.areaSweeps

    const loaded = WeatherBotState.decode(json)
    assert.deepStrictEqual(loaded.areaSweeps, [])
    assert.equal(Object.keys(loaded.warnings).length, 1, 'everything else in the blob is unchanged')
  })

  // A blob written between revisions 9 and 10 held exactly one sweep, under `areaSweep`. It is
  // lifted into the list rather than dropped: a map that was on screen before an update is still
  // on screen after it.
  it('a state blob holding revision 9 single sweep lifts it into the list', () => {
    const bot = new Bot()
    bot.apply(F.areaSweep({ seq: 1, group: 7, index: 0, total: 1, entries: [F.texasSweepEntry] }), F.t0)
    const json = JSON.parse(JSON.stringify(bot.state))
    json.areaSweep = json.areaSweeps[0]
    delete json.areaSweeps

    const loaded = WeatherBotState.decode(json)
    assert.equal(loaded.areaSweeps.length, 1)
    assert.equal(loaded.areaSweeps[0].group, 7)
    assert.equal(loaded.areaSweeps[0].isScoped, false, 'revision 9 sweeps were the whole country')
    assert.deepStrictEqual(loaded.areaSweeps[0].scope, [])
  })
})

// MARK: - Scoped sweeps (spec §7C, revision 10)

describe('WeatherStateReducer scoped area sweeps', () => {
  const held = (bot) => bot.state.areaSweeps[0] ?? null

  it("packet 0's scope entries are the assembly's scope", () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, group: 4, index: 0, total: 2, scope: [35, 42], entries: [F.texasSweepEntry]
      }),
      F.t0
    )
    assert.equal(held(bot).isScoped, true)
    assert.deepStrictEqual(held(bot).scope, [35, 42])
    // Oklahoma is in the scope with no entry of its own, which is the answer "nothing active at
    // this level there" — and the reason the scope is on the wire at all.
    assert.deepStrictEqual(WeatherAreaSweepAssembly.entries(held(bot)), [F.texasSweepEntry])
    assert.equal(WeatherAreaSweepAssembly.coversState(held(bot), { state: 35 }), true)
    assert.equal(WeatherAreaSweepAssembly.coversState(held(bot), { state: 5 }), false)
  })

  // Bit 7 is on every packet, so a phone that lost packet 0 still knows it is not looking at the
  // country — it just cannot say what it *is* looking at.
  it('a scoped sweep whose packet 0 is missing knows it is scoped and not what of', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, group: 4, index: 1, total: 2, isScoped: true, entries: [F.texasSweepEntry]
      }),
      F.t0
    )
    assert.equal(held(bot).isScoped, true)
    assert.equal(held(bot).scope, null, 'null is "not the country and I cannot say what"')
    assert.equal(WeatherAreaSweepAssembly.coversState(held(bot), { state: 42 }), false)

    // Packet 0 arrives late and settles it.
    bot.apply(
      F.areaSweep({
        seq: 2, group: 4, index: 0, total: 2, scope: [42], entries: [F.oklahomaSweepEntry]
      }),
      F.t0 + 1000
    )
    assert.deepStrictEqual(held(bot).scope, [42])
    assert.equal(WeatherAreaSweepAssembly.isComplete(held(bot)), true)
  })

  it('a packet past 0 never writes an empty scope over the one packet 0 gave', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({ seq: 1, group: 4, index: 0, total: 2, scope: [42], entries: [] }), F.t0
    )
    bot.apply(
      F.areaSweep({
        seq: 2, group: 4, index: 1, total: 2, isScoped: true, entries: [F.texasSweepEntry]
      }),
      F.t0 + 1000
    )
    assert.deepStrictEqual(held(bot).scope, [42], '"no states in this packet" is not "no states"')
  })

  // Design §2. A national sweep is the newest word on every state there is.
  it('a national sweep drops every sweep older than it', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, builtMinutes: F.t0Minutes, group: 1, index: 0, total: 1, scope: [42],
        entries: [F.texasSweepEntry]
      }),
      F.t0
    )
    bot.apply(
      F.areaSweep({
        seq: 2, builtMinutes: F.t0Minutes + 10, group: 2, index: 0, total: 1,
        entries: [F.oklahomaSweepEntry]
      }),
      F.t0 + 600_000
    )
    assert.equal(bot.state.areaSweeps.length, 1)
    assert.equal(held(bot).group, 2)
  })

  // ...and a scoped one never drops a national: it is still the only word on the other
  // forty-nine states.
  it('a scoped sweep leaves an older national sweep alone', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, builtMinutes: F.t0Minutes, group: 1, index: 0, total: 1,
        entries: [F.oklahomaSweepEntry]
      }),
      F.t0
    )
    bot.apply(
      F.areaSweep({
        seq: 2, builtMinutes: F.t0Minutes + 10, group: 2, index: 0, total: 1, scope: [42],
        entries: [F.texasSweepEntry]
      }),
      F.t0 + 600_000
    )
    assert.deepStrictEqual(bot.state.areaSweeps.map((one) => one.group), [2, 1], 'newest first')
  })

  it('a scoped sweep drops older scoped sweeps whose scope it fully contains, and no others', () => {
    const bot = new Bot()
    const send = (seq, minutes, group, scope) => bot.apply(
      F.areaSweep({
        seq, builtMinutes: F.t0Minutes + minutes, group, index: 0, total: 1, scope, entries: []
      }),
      F.t0 + minutes * 60_000
    )
    send(1, 0, 1, [42])          // Texas
    send(2, 10, 2, [35])         // Oklahoma: says nothing about Texas
    assert.deepStrictEqual(bot.state.areaSweeps.map((one) => one.group), [2, 1])

    send(3, 20, 3, [35, 42])     // both: everything the first two said, more recently
    assert.deepStrictEqual(bot.state.areaSweeps.map((one) => one.group), [3])

    send(4, 30, 4, [42])         // Texas again: says nothing about Oklahoma, so 3 stays
    assert.deepStrictEqual(bot.state.areaSweeps.map((one) => one.group), [4, 3])
  })

  it('a scoped sweep of unknown scope is neither dropped nor drops anything', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, builtMinutes: F.t0Minutes, group: 1, index: 1, total: 2, isScoped: true, entries: []
      }),
      F.t0
    )
    bot.apply(
      F.areaSweep({
        seq: 2, builtMinutes: F.t0Minutes + 10, group: 2, index: 0, total: 1, scope: [42], entries: []
      }),
      F.t0 + 600_000
    )
    assert.deepStrictEqual(bot.state.areaSweeps.map((one) => one.group), [2, 1])
  })

  it('at most eight sweeps are kept', () => {
    const bot = new Bot()
    for (let index = 0; index < 12; index += 1) {
      bot.apply(
        F.areaSweep({
          seq: index + 1, builtMinutes: F.t0Minutes + index, group: index + 1, index: 0, total: 1,
          scope: [index + 1], entries: []
        }),
        F.t0 + index * 60_000
      )
    }
    assert.equal(bot.state.areaSweeps.length, WeatherStateReducer.maxAreaSweeps)
    assert.deepStrictEqual(bot.state.areaSweeps.map((one) => one.group), [12, 11, 10, 9, 8, 7, 6, 5])
  })

  /**
   * The rule `>part` rests on. The bot answers by sending the named packets again — identical
   * bytes except a new `seq` in byte 0 — and by then the sweep they belong to is not the newest
   * thing held. Keyed on "the newest sweep" the resend would be dropped as older than what is
   * held, which turns "4 of 7 parts arrived" into "4 of 7 parts arrived, for ever".
   */
  it('a re-sent part fills the hole in its own assembly, not the newest one', () => {
    const bot = new Bot()
    bot.apply(
      F.areaSweep({
        seq: 1, builtMinutes: F.t0Minutes, group: 1, index: 0, total: 2,
        entries: [F.texasSweepEntry]
      }),
      F.t0
    )
    // A scoped sweep of Texas arrives in between and is the newest thing held.
    bot.apply(
      F.areaSweep({
        seq: 2, builtMinutes: F.t0Minutes + 5, group: 2, index: 0, total: 1, scope: [42],
        entries: [F.texasSweepEntry]
      }),
      F.t0 + 300_000
    )
    assert.deepStrictEqual(bot.state.areaSweeps.map((one) => one.group), [2, 1])

    // `>part 1 1`: the missing packet, with a fresh seq and the original group and build time.
    const changes = bot.apply(
      F.areaSweep({
        seq: 40, builtMinutes: F.t0Minutes, group: 1, index: 1, total: 2,
        entries: [F.oklahomaSweepEntry]
      }),
      F.t0 + 360_000
    )
    assert.deepStrictEqual(
      changes[changes.length - 1],
      WeatherStateChange.areaSweepStored({ group: 1, index: 1, isComplete: true })
    )
    const national = bot.state.areaSweeps.find((one) => one.group === 1)
    assert.equal(WeatherAreaSweepAssembly.isComplete(national), true)
    assert.deepStrictEqual(
      WeatherAreaSweepAssembly.entries(national), [F.texasSweepEntry, F.oklahomaSweepEntry]
    )
    assert.equal(national.firstReceivedAt, F.t0, 'the assembly is the one it always was')
    assert.equal(national.lastReceivedAt, F.t0 + 360_000)
  })

  // The resend carries a new `seq`, so the duplicate window never sees it as a copy — and the
  // gap it opens is a gap like any other, which the assembly is indifferent to.
  it('a re-sent part is not a duplicate and is not out of order', () => {
    const bot = new Bot()
    const first = F.areaSweep({ seq: 5, group: 1, index: 0, total: 2, entries: [F.texasSweepEntry] })
    bot.apply(first, F.t0)
    // The same packet again under the same seq *is* a copy and changes nothing.
    assert.deepStrictEqual(
      bot.apply(first, F.t0 + 1000), [WeatherStateChange.duplicate({ seq: 5 })]
    )
    const resent = bot.apply(
      F.areaSweep({ seq: 9, group: 1, index: 1, total: 2, entries: [F.oklahomaSweepEntry] }),
      F.t0 + 20_000
    )
    assert.ok(resent.some((change) => change.kind === 'areaSweepStored' && change.isComplete))
  })
})

// MARK: - Radar tiles (spec §7D, revision 11)

describe('WeatherStateReducer radar tiles', () => {
  const dallas = { south: 32, west: -98, zoom: 0 }
  const austin = { south: 29, west: -99, zoom: 0 }
  const tileFor = (state, tile) => (state.radarTiles ?? []).find(
    (one) => one.tile.south === tile.south && one.tile.west === tile.west && one.tile.zoom === tile.zoom
  ) ?? null

  it('a tile is stored under its lattice square, with the source the bot stated', () => {
    const bot = new Bot()
    const changes = bot.apply(
      F.radar({ seq: 1, south: 32, west: -98, rows: F.radarRows({ cells: [[19, 19, 2]] }) }), F.t0
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.radarStored(dallas, { takenMinutes: F.t0Minutes })
    ])
    const stored = tileFor(bot.state, dallas)
    assert.deepStrictEqual(stored.tile, dallas)
    assert.equal(stored.receivedAt, F.t0)
    assert.equal(stored.source, 1, 'off the dish')
    assert.equal(WeatherStoredRadarTile.takenAt(stored), dateFromUnixMinutes(F.t0Minutes))
    assert.equal(MeshWXRadar.level(stored.radar, { row: 19, col: 19 }), 2)
  })

  it('a newer picture of the same square replaces the one held', () => {
    const bot = new Bot()
    bot.apply(F.radar({ seq: 1, south: 32, west: -98 }), F.t0)
    bot.apply(
      F.radar({
        seq: 2, south: 32, west: -98, takenMinutes: F.t0Minutes + 15,
        rows: F.radarRows({ cells: [[0, 0, 3]] })
      }),
      F.t0 + minutes(15)
    )
    assert.equal(bot.state.radarTiles.length, 1, 'one picture per square: no history, no animation')
    assert.equal(tileFor(bot.state, dallas).radar.taken_min, F.t0Minutes + 15)
  })

  it('an older picture of a square already held changes nothing', () => {
    const bot = new Bot()
    bot.apply(F.radar({ seq: 1, south: 32, west: -98, takenMinutes: F.t0Minutes }), F.t0)
    const changes = bot.apply(
      F.radar({ seq: 2, south: 32, west: -98, takenMinutes: F.t0Minutes - 15 }), F.t0 + 1000
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.radarIgnoredOlder({ takenMinutes: F.t0Minutes - 15 })
    ])
    assert.equal(tileFor(bot.state, dallas).radar.taken_min, F.t0Minutes)
  })

  /**
   * The coarse tile is the same picture at half the detail, cut because somebody else's request
   * could not fit the finer one in a packet. Taking it would throw away detail this phone has.
   */
  it('a coarse picture never replaces a fine one of the same taken', () => {
    const bot = new Bot()
    bot.apply(F.radar({ seq: 1, south: 32, west: -98 }), F.t0)
    const changes = bot.apply(
      F.radar({ seq: 2, south: 32, west: -98, rows: F.radarRows({ size: 16 }) }), F.t0 + 1000
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.radarIgnoredOlder({ takenMinutes: F.t0Minutes })
    ])
    assert.equal(tileFor(bot.state, dallas).radar.coarse, false)

    // The other way round it does replace: the fine tile is more of the same picture.
    const other = new Bot()
    other.apply(F.radar({ seq: 1, south: 32, west: -98, rows: F.radarRows({ size: 16 }) }), F.t0)
    other.apply(F.radar({ seq: 2, south: 32, west: -98 }), F.t0 + 1000)
    assert.equal(tileFor(other.state, dallas).radar.coarse, false)
  })

  it('two squares are two tiles, newest taken first', () => {
    const bot = new Bot()
    bot.apply(F.radar({ seq: 1, south: 32, west: -98, takenMinutes: F.t0Minutes }), F.t0)
    bot.apply(F.radar({ seq: 2, south: 29, west: -99, takenMinutes: F.t0Minutes + 15 }), F.t0 + minutes(15))
    assert.deepStrictEqual(
      bot.state.radarTiles.map((one) => one.tile), [austin, dallas]
    )
  })

  /**
   * "The bot clock" is read off the pictures themselves: a Radar message carries no other clock,
   * and a tile drained from the radio's queue at connect is as old as the picture says it is,
   * whenever the phone heard it.
   */
  it('a picture more than three hours behind the newest is dropped', () => {
    const bot = new Bot()
    bot.apply(F.radar({ seq: 1, south: 32, west: -98, takenMinutes: F.t0Minutes - 200 }), F.t0)
    assert.equal(bot.state.radarTiles.length, 1)
    bot.apply(F.radar({ seq: 2, south: 29, west: -99, takenMinutes: F.t0Minutes }), F.t0 + 1000)
    assert.deepStrictEqual(bot.state.radarTiles.map((one) => one.tile), [austin])

    // And the other way about: a picture that old arriving now says so and is not kept.
    const changes = bot.apply(
      F.radar({ seq: 3, south: 24, west: -100, takenMinutes: F.t0Minutes - 200 }), F.t0 + 2000
    )
    assert.deepStrictEqual(changes, [
      WeatherStateChange.radarIgnoredOlder({ takenMinutes: F.t0Minutes - 200 })
    ])
    assert.equal(bot.state.radarTiles.length, 1)
  })

  it('at most twelve tiles are held, the oldest taken dropped', () => {
    const bot = new Bot()
    for (let index = 0; index < 14; index += 1) {
      bot.apply(
        F.radar({
          seq: index + 1,
          south: 20 + index,
          west: -98,
          takenMinutes: F.t0Minutes - 14 + index
        }),
        F.t0 + index * 1000
      )
    }
    assert.equal(bot.state.radarTiles.length, WeatherStateReducer.maxRadarTiles)
    assert.deepStrictEqual(
      bot.state.radarTiles.map((one) => one.tile.south),
      [33, 32, 31, 30, 29, 28, 27, 26, 25, 24, 23, 22]
    )
  })

  it('a tile survives the round trip through persisted JSON', () => {
    const bot = new Bot()
    bot.apply(
      F.radar({
        seq: 1, south: 24, west: -100, zoom: 1, rows: F.radarRows({ size: 16, cells: [[2, 3, 1]] }),
        bounds: [0, 9, 0, 15]
      }),
      F.t0
    )
    const read = WeatherBotState.decode(JSON.parse(JSON.stringify(bot.state)))
    assert.deepStrictEqual(read.radarTiles, bot.state.radarTiles)
    assert.deepStrictEqual(read.radarTiles[0].radar.bounds, [0, 9, 0, 15])
  })

  /** A state file written before revision 11 has no tiles, which is exactly what it held. */
  it('a state file written before revision 11 reads as holding none', () => {
    const before = WeatherBotState.make({ botID: F.botID })
    delete before.radarTiles
    const read = WeatherBotState.decode(JSON.parse(JSON.stringify(before)))
    assert.deepStrictEqual(read.radarTiles, [])
  })
})
