// Port of MC1ServicesTests/Weather/Screen/WeatherAlertMapTests.swift (docs/PORTING.md).
//
// Spec revision 10's alert map: several sweeps resolved into one picture, the offer to re-request
// a hole in one, the selection the next tap asks for, and what that tap costs.

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { MeshWXTables } from '../src/meshwx/index.js'
import { nodeBundleLoader } from '../src/meshwx/nodeLoader.js'
import {
  WeatherAreaSweepAssembly,
  WeatherPartsKind,
  WeatherRequest,
  WeatherTextAssembly
} from '../src/weather/index.js'
import {
  WeatherAlertMapPicture,
  WeatherAreaSelection,
  WeatherAreaSelectionStore,
  WeatherAreaSweepCost,
  WeatherPartsOffer
} from '../src/screen/index.js'

const tables = await MeshWXTables.load(nodeBundleLoader())
const TX = tables.states.indexOf('TX')
const OK = tables.states.indexOf('OK')
const MT = tables.states.indexOf('MT')

const t0 = Date.UTC(2026, 8, 20, 18, 0, 0)
const t0Minutes = Math.floor(t0 / 60000)

const entry = (event, state, start = 1, run = 1) => ({ event, state, county: false, start, run })

/** A sweep as the reducer would have left it: `scope` null means scoped with packet 0 missing. */
function sweep({
  group,
  minutes = 0,
  scope = null,
  packets = { 0: [] },
  total = 1,
  includesAdvisories = false,
  wasCut = false,
  receivedAt = t0
}) {
  return WeatherAreaSweepAssembly.make({
    builtMinutes: t0Minutes + minutes,
    group,
    total,
    packets,
    firstReceivedAt: receivedAt,
    lastReceivedAt: receivedAt,
    includesAdvisories,
    wasCut,
    isScoped: scope !== 'national',
    scope: scope === 'national' ? [] : scope
  })
}

const national = (options) => sweep({ ...options, scope: 'national' })

describe('WeatherAlertMapPicture', () => {
  it('nothing held is an empty picture that covers nothing', () => {
    const picture = WeatherAlertMapPicture.make({ sweeps: [], states: tables.states, now: t0 })
    assert.deepStrictEqual(picture.parts, [])
    assert.deepStrictEqual(picture.entries, [])
    assert.equal(picture.coversWholeCountry, false)
  })

  /**
   * The rule the whole screen rests on: "for each state the newest sweep whose scope includes it
   * wins, and only that sweep's entries for that state are drawn". Without it a phone that asked
   * for Texas at 13:40 and held the country from 13:20 would draw both, and every Texas alert
   * that ended in those twenty minutes would still be on the map under this hour's.
   */
  it('the newest sweep covering a state is the only one drawn for it', () => {
    const older = national({
      group: 1, minutes: 0, packets: { 0: [entry(3, TX, 100), entry(24, OK, 1), entry(11, MT, 5)] },
    })
    const newer = sweep({
      group: 2, minutes: 20, scope: [TX], packets: { 0: [entry(1, TX, 453)] }, receivedAt: t0 + 60_000,
    })
    const picture = WeatherAlertMapPicture.make({ sweeps: [newer, older], states: tables.states, now: t0 })

    assert.deepStrictEqual(picture.parts.map((part) => part.group), [2, 1], 'newest first')
    assert.deepStrictEqual(picture.parts[0].stateCodes, ['TX'])
    assert.deepStrictEqual(
      picture.parts[1].stateCodes, [], 'a national sweep is "the rest of the country"',
    )
    assert.equal(picture.coversWholeCountry, true)

    // Texas comes from the scoped sweep and from nowhere else; the rest from the national one.
    assert.deepStrictEqual(
      picture.entries.map((one) => [one.event, one.state, one.part]),
      [[1, TX, 0], [24, OK, 1], [11, MT, 1]],
    )
  })

  it('with nothing national held the picture does not cover the country', () => {
    const picture = WeatherAlertMapPicture.make({
      sweeps: [sweep({ group: 2, scope: [TX, OK], packets: { 0: [entry(3, TX, 100)] } })],
      states: tables.states,
      now: t0,
    })
    assert.equal(picture.coversWholeCountry, false)
    // Oklahoma is in the scope and has no entry: nothing is active there at this level, which is
    // an answer and the reason the scope is on the wire at all.
    assert.deepStrictEqual(picture.parts[0].stateCodes, ['OK', 'TX'])
    assert.equal(picture.entries.length, 1)
  })

  /** "A scoped sweep whose scope is unknown (packet 0 missing) contributes its entries but wins no state." */
  it('a scoped sweep of unknown scope draws its entries and takes no state', () => {
    const unplaced = sweep({
      group: 3, minutes: 20, scope: null, total: 2, packets: { 1: [entry(3, TX, 100)] },
      receivedAt: t0 + 60_000,
    })
    const held = national({ group: 1, minutes: 0, packets: { 0: [entry(24, TX, 200)] } })
    const picture = WeatherAlertMapPicture.make({
      sweeps: [unplaced, held], states: tables.states, now: t0,
    })
    assert.deepStrictEqual(picture.parts[0].stateCodes, [], 'it cannot name a state, so it wins none')
    assert.equal(picture.parts[0].scope, null)
    // Both are drawn: the national sweep is still the newest word on Texas, and the unplaced one
    // is the only picture this phone has of what it carries.
    assert.deepStrictEqual(picture.entries.map((one) => one.part), [0, 1])
  })

  it('a part carries what its status line says', () => {
    const picture = WeatherAlertMapPicture.make({
      sweeps: [sweep({
        group: 9,
        minutes: 5,
        scope: [TX],
        total: 7,
        packets: { 0: [entry(3, TX, 100)], 2: [], 3: [] },
        includesAdvisories: true,
        wasCut: true,
        receivedAt: t0 + 30_000,
      })],
      states: tables.states,
      now: t0,
    })
    const part = picture.parts[0]
    assert.equal(part.group, 9)
    assert.equal(part.builtAt, (t0Minutes + 5) * 60_000)
    assert.equal(part.includesAdvisories, true)
    assert.equal(part.wasCut, true)
    assert.equal(part.isScoped, true)
    assert.deepStrictEqual(part.scope, [TX])
    assert.equal(part.receivedPackets, 3)
    assert.equal(part.totalPackets, 7)
    assert.deepStrictEqual(part.missingIndexes, [1, 4, 5, 6])
    assert.equal(part.firstReceivedAt, t0 + 30_000)
    assert.equal(WeatherAlertMapPicture.isPartial(picture), true)
  })
})

describe('WeatherPartsOffer', () => {
  const partial = () => sweep({ group: 12, total: 3, packets: { 0: [] }, receivedAt: t0 })

  it('offers the holes once the bot\'s own resend has had its chance', () => {
    // Under 15 s: the bot resends an unechoed packet 8-10 s later of its own accord, and asking
    // before that has passed asks for a packet already on the air.
    assert.equal(
      WeatherPartsOffer.make({ assembly: partial(), kind: WeatherPartsKind.areaSweep, now: t0 + 14_000 }),
      null,
    )
    assert.deepStrictEqual(
      WeatherPartsOffer.make({ assembly: partial(), kind: WeatherPartsKind.areaSweep, now: t0 + 15_000 }),
      WeatherRequest.parts({ group: 12, indexes: [1, 2], of: WeatherPartsKind.areaSweep }),
    )
  })

  it('stops offering once the bot no longer holds the bytes', () => {
    // PARTS_CACHE_S is 600: past it `>part` comes back Not available, and the ordinary ask is
    // the only offer there is.
    assert.notEqual(
      WeatherPartsOffer.make({ assembly: partial(), kind: WeatherPartsKind.areaSweep, now: t0 + 600_000 }),
      null,
    )
    assert.equal(
      WeatherPartsOffer.make({ assembly: partial(), kind: WeatherPartsKind.areaSweep, now: t0 + 601_000 }),
      null,
    )
  })

  it('offers nothing for a complete assembly', () => {
    const whole = sweep({ group: 12, total: 2, packets: { 0: [], 1: [] } })
    assert.equal(
      WeatherPartsOffer.make({ assembly: whole, kind: WeatherPartsKind.areaSweep, now: t0 + 60_000 }),
      null,
    )
  })

  it('works the same for a text reply, and names the subject for the log', () => {
    const reply = WeatherTextAssembly.make({
      subject: 1,
      group: 212,
      total: 3,
      chunks: { 0: 'AREA', 2: 'DISCUSSION' },
      firstReceivedAt: t0,
      lastReceivedAt: t0,
    })
    const kind = WeatherPartsKind.text({ subject: 1 })
    const request = WeatherPartsOffer.make({ assembly: reply, kind, now: t0 + 20_000 })
    assert.deepStrictEqual(request, WeatherRequest.parts({ group: 212, indexes: [1], of: kind }))
    assert.equal(WeatherRequest.wireText(request), '>part 212 1')
  })
})

describe('WeatherAreaSelection', () => {
  it('normalises to the form the wire and the key both use', () => {
    const selection = WeatherAreaSelection.make({ states: ['tx', ' ok ', 'TX'] })
    assert.deepStrictEqual(selection, { isWholeCountry: false, states: ['OK', 'TX'] })
    assert.equal(
      WeatherRequest.wireText(WeatherAreaSelection.request(selection, { includesAdvisories: true })),
      '>wmap all OKTX',
    )
  })

  it('no states at all is the whole country', () => {
    assert.deepStrictEqual(WeatherAreaSelection.make({ states: [] }), WeatherAreaSelection.wholeCountry)
    assert.deepStrictEqual(
      WeatherAreaSelection.toggling(WeatherAreaSelection.make({ states: ['TX'] }), { state: 'TX' }),
      WeatherAreaSelection.wholeCountry,
    )
  })

  /**
   * Fifteen codes are the whole 40-byte request budget. Silently dropping the sixteenth would
   * draw a map missing a state the user picked, so the tap asks for the country and the screen
   * says so before it.
   */
  it('more than fifteen states asks for the whole country', () => {
    const many = WeatherAreaSelection.make({
      states: ['AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'IA', 'ID', 'IL', 'IN', 'KS'],
    })
    assert.equal(many.states.length, 16)
    assert.equal(WeatherAreaSelection.asksWholeCountry(many), true)
    assert.equal(
      WeatherRequest.wireText(WeatherAreaSelection.request(many, { includesAdvisories: false })),
      '>wmap',
    )
    const fifteen = WeatherAreaSelection.make({ states: many.states.slice(0, 15) })
    assert.equal(WeatherAreaSelection.asksWholeCountry(fifteen), false)
  })

  it('defaults to the page\'s state, and to the country with no place', () => {
    const defaults = new Map()
    const store = new WeatherAreaSelectionStore({
      defaults: { get: (key) => defaults.get(key) ?? null, set: (key, value) => defaults.set(key, value) },
    })
    assert.equal(store.hasSelection, false)
    assert.deepStrictEqual(store.selection({ defaultStateCode: 'TX' }), { isWholeCountry: false, states: ['TX'] })
    assert.deepStrictEqual(store.selection(), WeatherAreaSelection.wholeCountry)

    store.setSelection(WeatherAreaSelection.make({ states: ['OK', 'TX'] }))
    assert.equal(store.hasSelection, true)
    // Once it is chosen the page's place no longer decides it: the user answered the question.
    assert.deepStrictEqual(
      store.selection({ defaultStateCode: 'MT' }), { isWholeCountry: false, states: ['OK', 'TX'] },
    )
    // And it survives a round trip through persistence.
    const written = JSON.parse(JSON.stringify(defaults.get('weather.areaSelection')))
    assert.deepStrictEqual(WeatherAreaSelection.decode(written), { isWholeCountry: false, states: ['OK', 'TX'] })
  })
})

describe('WeatherAreaSweepCost', () => {
  const picture = (sweeps) => WeatherAlertMapPicture.make({ sweeps, states: tables.states, now: t0 })
  const cost = (selection, advisories, held) =>
    WeatherAreaSweepCost.packets({ for: selection, advisories, held, tables })

  it('the whole country costs what the last national sweep at that level did', () => {
    const held = picture([national({ group: 1, total: 6, packets: { 0: [] } })])
    assert.equal(cost(WeatherAreaSelection.wholeCountry, false, held), 6)
    // A narrow map says nothing about how much more the advisories would add.
    assert.equal(cost(WeatherAreaSelection.wholeCountry, true, held), 7)
  })

  it('with nothing held the country is the measured figure', () => {
    const empty = picture([])
    assert.equal(cost(WeatherAreaSelection.wholeCountry, false, empty), 4)
    assert.equal(cost(WeatherAreaSelection.wholeCountry, true, empty), 7)
  })

  it('with nothing held a few states are one packet per four', () => {
    const empty = picture([])
    assert.equal(cost(WeatherAreaSelection.make({ states: ['TX'] }), false, empty), 1)
    assert.equal(cost(WeatherAreaSelection.make({ states: ['TX', 'OK', 'MT', 'CA', 'NV'] }), false, empty), 2)
  })

  /** `ceil((entries in those states + states) / 38)`, at least 1: the scope entries cost too. */
  it('a few states are measured against what the map already says about them', () => {
    const entries = []
    for (let index = 0; index < 40; index += 1) entries.push(entry(3, TX, 100 + index))
    for (let index = 0; index < 40; index += 1) entries.push(entry(24, MT, 1 + index))
    const held = picture([national({ group: 1, total: 4, packets: { 0: entries } })])

    // 40 Texas entries and one scope entry: two packets.
    assert.equal(cost(WeatherAreaSelection.make({ states: ['TX'] }), false, held), 2)
    assert.equal(cost(WeatherAreaSelection.make({ states: ['MT'] }), false, held), 2)
    // Both: 80 entries and two scope entries, three packets.
    assert.equal(cost(WeatherAreaSelection.make({ states: ['TX', 'MT'] }), false, held), 3)
    // A state with nothing in it is still a packet: the scope entry says it is clear.
    assert.equal(cost(WeatherAreaSelection.make({ states: ['OK'] }), false, held), 1)
  })
})
