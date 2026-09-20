// Port of MC1Tests/Views/Tools/Weather/WeatherToolModelTests.swift (docs/PORTING.md).
//
// The model's request bookkeeping and its per-page rules, without a radio, plus the two suites
// that live in the same file: when a new fix is worth moving the place for, and when the map
// outlines are worth loading.
//
// Not ported: `the visit's model survives while the tool is open` (it tests `WeatherModelStore`,
// the iOS shell-swap workaround, which the brief leaves out) and the nine `WeatherPlaceSearch`
// cases that drive `WeatherPlacePickerView` — a `*View.swift`, so `src/ui` (docs/PORTING.md §2).

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { MeshWXEncoder, MeshWXTables } from '../src/meshwx/index.js'
import {
  WeatherBotState,
  WeatherRequest,
  WeatherRequestOutcome,
  WeatherStateReducer,
  WeatherStoredDigest,
  WeatherStoredForecast,
  WeatherSessionInfo,
  WeatherStoredWarning,
  WeatherTextAssembly,
  WeatherTrafficEntry,
  WeatherTrafficLog,
} from '../src/weather/index.js'
import {
  WeatherAreaSelection,
  WeatherCoverage,
  WeatherPage,
  WeatherPlaceAreas,
  WeatherSavedPlace,
  WeatherScreenSnapshot,
  WeatherStations,
  WeatherUpdatePlan,
} from '../src/screen/index.js'
import {
  WeatherAlertTarget,
  WeatherAnswerNote,
  WeatherPlacePickerAction,
  WeatherScreenBuilder,
  WeatherToolModel,
} from '../src/app/index.js'
import * as F from './helpers/app-fixture.js'

const tables = await F.loadTables()

const botID = 0x041d

/** When a new fix is worth moving the place for. */
describe('Weather location samples', () => {
  const sample = ({ latitude = 30.2672, accuracy = 50, after = 0 } = {}) => ({
    latitude,
    longitude: -97.7431,
    horizontalAccuracy: accuracy,
    timestamp: F.now + after * 1000,
  })

  it("a survey stream's next fix a second later changes nothing", () => {
    assert.equal(
      WeatherToolModel.isMeaningfulChange({ from: sample(), to: sample({ latitude: 30.2673, after: 1 }) }),
      false,
    )
  })

  it('a fix a minute newer replaces the place', () => {
    assert.equal(WeatherToolModel.isMeaningfulChange({ from: sample(), to: sample({ after: 61 }) }), true)
  })

  it('a fix half a kilometre away replaces the place', () => {
    assert.equal(
      WeatherToolModel.isMeaningfulChange({ from: sample(), to: sample({ latitude: 30.3, after: 5 }) }),
      true,
    )
  })

  it('a much more accurate fix replaces a coarse one, a slightly better one does not', () => {
    assert.equal(
      WeatherToolModel.isMeaningfulChange({
        from: sample({ accuracy: 3000 }),
        to: sample({ accuracy: 65, after: 5 }),
      }),
      true,
    )
    assert.equal(
      WeatherToolModel.isMeaningfulChange({
        from: sample({ accuracy: 100 }),
        to: sample({ accuracy: 60, after: 5 }),
      }),
      false,
    )
    assert.equal(
      WeatherToolModel.isMeaningfulChange({
        from: sample({ accuracy: -1 }),
        to: sample({ accuracy: 60, after: 5 }),
      }),
      true,
    )
    assert.equal(
      WeatherToolModel.isMeaningfulChange({
        from: sample({ accuracy: 100 }),
        to: sample({ accuracy: -1, after: 5 }),
      }),
      false,
    )
  })
})

describe('Weather tool model', () => {
  it('a request is pending from the tap, before the service has answered', () => {
    const { model } = F.makeModel()
    model.beginRequest(WeatherRequest.digest)
    assert.equal(model.status({ for: WeatherRequest.digest }).kind, 'pending')
    assert.deepEqual(model.activeRequest, WeatherRequest.digest)
    model.endRequest(WeatherRequest.digest)
    assert.equal(model.activeRequest, null)
    // No snapshot yet, so nothing can be asked: the block shows, not the old pending state.
    assert.deepEqual(model.status({ for: WeatherRequest.digest }), { kind: 'blocked', value: 'noBot' })
  })

  // With a snapshot and nothing blocking, every request used to read "No weather radio to ask
  // yet" and nothing was sent, picking a town included.
  it('a snapshot with no block can be asked, and only no snapshot means no weather radio', () => {
    const bot = {
      publicKey: new Uint8Array([0x1d, 0x04, ...new Array(30).fill(0x55)]),
      name: 'WX-AUS',
      latitude: 0,
      longitude: 0,
      lastAdvert: null,
    }
    const snapshot = (connected) =>
      WeatherScreenSnapshot.make(
        WeatherScreenSnapshot.Inputs.make({
          states: {},
          bots: [bot],
          preferredBotID: null,
          place: null,
          isRadioConnected: connected,
          firmwareSupportsWeather: true,
          firmwareVersion: 'v1.15.0',
          hasWeatherChannel: true,
          session: WeatherSessionInfo.make({ startedAt: F.now }),
          now: F.now,
          timeZone: F.timeZone,
        }),
        { geometry: { isLoaded: false, distanceKilometres: () => null, centre: () => null }, tables },
      )
    const status = (one) =>
      WeatherToolModel.status({
        for: WeatherRequest.forecast({ point: 103 }),
        snapshot: one,
        pending: [],
        inFlight: [],
        outcomes: {},
        now: F.now,
      })

    const ready = snapshot(true)
    assert.equal(ready.requestBlock, null)
    assert.deepEqual(status(ready), { kind: 'idle' })
    assert.deepEqual(status(snapshot(false)), { kind: 'blocked', value: 'radioOffline' })
    assert.deepEqual(status(null), { kind: 'blocked', value: 'noBot' })
  })

  it('a missing warning refused since its list arrived is passed over', () => {
    const listed = F.now
    const outcome = (request, kind, at) => [
      WeatherRequest.wireText(request),
      { outcome: kind, at, request },
    ]
    const outcomes = Object.fromEntries([
      outcome(
        WeatherRequest.warning({ identity: 'SV.W.EWX.42' }),
        WeatherRequestOutcome.notAvailable(0),
        listed + 60_000,
      ),
      outcome(
        WeatherRequest.warning({ identity: 'SV.W.EWX.43' }),
        WeatherRequestOutcome.timedOut({ botWasHeard: false }),
        listed + 60_000,
      ),
      outcome(
        WeatherRequest.warning({ identity: 'SV.W.EWX.44' }),
        WeatherRequestOutcome.notAvailable(0),
        listed - 60_000,
      ),
      outcome(WeatherRequest.digest, WeatherRequestOutcome.notAvailable(0), listed + 60_000),
    ])
    assert.deepEqual(WeatherToolModel.notAvailableIdentities({ outcomes, since: listed, tables }), [
      { event: 3, office: 35, etn: 42 },
    ])
  })

  it('a second call clears only the fingerprint it recorded', () => {
    const { model } = F.makeModel()
    const first = 'first'
    const second = 'second'
    model.recordFingerprint({ token: first, value: 1, kind: 'other' }, { for: WeatherRequest.digest })
    model.recordFingerprint({ token: second, value: 2, kind: 'other' }, { for: WeatherRequest.digest })
    model.clearFingerprint({ for: WeatherRequest.digest, token: first })
    assert.equal(model.fingerprints['>d']?.token, second)
    model.clearFingerprint({ for: WeatherRequest.digest, token: second })
    assert.equal(model.fingerprints['>d'], undefined)
  })

  it('a forecast answer with the same issue time is nothing new', () => {
    const state = WeatherBotState.make({ botID })
    state.forecasts['103'] = WeatherStoredForecast.make({
      forecast: { point: 103, issued_min: 29_000_000, first_period: 0, periods: [] },
      receivedAt: F.now,
    })
    const before = WeatherToolModel.fingerprint(WeatherRequest.forecast({ point: 103 }), {
      sourceBotID: botID,
      states: { [String(botID)]: state },
      tables,
    })
    assert.notEqual(before, null)
    assert.equal(before.kind, WeatherAnswerNote.Kind.forecast)
    assert.equal(before.value, 29_000_000)
    state.forecasts['103'].forecast.issued_min = 29_000_060
    const after = WeatherToolModel.fingerprint(WeatherRequest.forecast({ point: 103 }), {
      sourceBotID: botID,
      states: { [String(botID)]: state },
      tables,
    })
    assert.notEqual(after?.value, before.value)
  })

  it('alert lists and text replies have fingerprints too', () => {
    const state = WeatherBotState.make({ botID })
    state.digest = WeatherStoredDigest.make({
      digest: { now_min: 50, feed_health: 2, entries: [] },
      receivedAt: F.now,
    })
    const states = { [String(botID)]: state }
    assert.equal(
      WeatherToolModel.fingerprint(WeatherRequest.digest, { sourceBotID: botID, states, tables })?.value,
      50,
    )

    const textPrint = () =>
      WeatherToolModel.fingerprint(WeatherRequest.spaceWeather, { sourceBotID: botID, states, tables })?.value
    assert.equal(textPrint(), null)
    // Somebody else's reply on the same subject is not an answer to this phone.
    state.texts['3'] = WeatherTextAssembly.make({
      subject: 2,
      group: 3,
      total: 1,
      chunks: { 0: 'Kp 3' },
      firstReceivedAt: F.now,
      lastReceivedAt: F.now,
    })
    assert.equal(textPrint(), null)

    state.texts['5'] = WeatherTextAssembly.make({
      subject: 2,
      group: 5,
      total: 1,
      chunks: { 0: 'Kp 4' },
      firstReceivedAt: F.now,
      lastReceivedAt: F.now,
      request: WeatherRequest.spaceWeather,
    })
    const one = textPrint()
    assert.notEqual(one, null)
    state.texts['5'].lastReceivedAt = F.now + 60_000
    assert.equal(textPrint(), one)
    state.texts['4'] = WeatherTextAssembly.make({
      subject: 2,
      group: 4,
      total: 1,
      chunks: { 0: 'Kp 6' },
      firstReceivedAt: F.now,
      lastReceivedAt: F.now,
    })
    assert.equal(textPrint(), one)
    state.texts['9'] = WeatherTextAssembly.make({
      subject: 2,
      group: 9,
      total: 1,
      chunks: { 0: 'Kp 5' },
      firstReceivedAt: F.now,
      lastReceivedAt: F.now,
      request: WeatherRequest.spaceWeather,
    })
    assert.notEqual(textPrint(), one)
  })

  it('only a complete reply this phone asked for, under five minutes old, replaces the button', () => {
    const owned = WeatherTextAssembly.make({
      subject: 5,
      group: 7,
      total: 1,
      chunks: { 0: 'KAUS 150553Z' },
      firstReceivedAt: F.now,
      lastReceivedAt: F.now,
      request: WeatherRequest.metar({ station: 'KAUS' }),
    })
    const metar = WeatherRequest.metar({ station: 'KAUS' })
    assert.equal(WeatherToolModel.isFreshOwnedReply(owned, { for: metar, now: F.now + 60_000 }), true)
    assert.equal(WeatherToolModel.isFreshOwnedReply(owned, { for: metar, now: F.now + 5 * 60_000 }), false)
    assert.equal(
      WeatherToolModel.isFreshOwnedReply(owned, { for: WeatherRequest.taf({ station: 'KAUS' }), now: F.now }),
      false,
    )
    assert.equal(
      WeatherToolModel.isFreshOwnedReply({ ...owned, total: 2 }, { for: metar, now: F.now }),
      false,
    )
    assert.equal(
      WeatherToolModel.isFreshOwnedReply({ ...owned, request: null }, { for: metar, now: F.now }),
      false,
    )
  })

  // A bare `>o` comes back with the batch the bot would send now, so it can refresh a station
  // that batch still carries and nothing else. Being in the footprint — any multi-station batch
  // of the last day — is not enough.
  it('a station the newest batch no longer carries is asked for by its code', () => {
    const nowMinutes = Math.floor(F.now / 60000)
    const place = F.place({ latitude: 30.2672, longitude: -97.7431, label: 'Austin, TX' })
    let state = WeatherBotState.make({ botID })
    const batch = (seq, minutes, stations) => {
      state = WeatherStateReducer.apply(
        {
          seq,
          bot: botID,
          type: 4,
          name: 'observations',
          flags: 0,
          ts_min: minutes,
          source: 0,
          stations: stations.map((station) => F.observation({ station, tempF: 88, sky: 1 })),
        },
        { to: state, receivedAt: F.now },
      ).state
    }
    batch(1, nowMinutes - 60, [202, 860, 194])
    batch(2, nowMinutes, [202, 860])

    const states = { [String(botID)]: state }
    const coverage = WeatherCoverage.make({ states, tables, now: F.now, areas: WeatherPlaceAreas.shared() })
    const readings = WeatherStations.readings({ states, coverage, place, tables, now: F.now })
    const carried = readings.find((one) => one.index === 202)
    assert.notEqual(carried, undefined)
    assert.deepEqual(WeatherUpdatePlan.readingsRequest({ for: carried }), WeatherRequest.observations)

    const dropped = readings.find((one) => one.index === 194)
    assert.notEqual(dropped, undefined)
    assert.equal(dropped.isInFootprint, true)
    assert.deepEqual(
      WeatherUpdatePlan.readingsRequest({ for: dropped }),
      WeatherRequest.observation({ station: dropped.station.icao }),
    )
  })

  // Picking keeps the place and sends nothing: the automatic forecast request on a pick is gone
  // (docs/MESHWX_UI.md §3.1 O-1, overturned), and Update is on the screen behind.
  it('a pick from Places is applied and kept, and sends nothing', () => {
    const { model } = F.makeModel()
    const town = WeatherSavedPlace.make({
      label: 'Round Rock, TX',
      latitude: 30.5,
      longitude: -97.7,
      chosenAt: F.now,
    })
    model.pendingPlaceAction = WeatherPlacePickerAction.place(town)
    assert.equal(model.applyPendingPlaceAction({ isLocationAuthorized: false }), false)
    assert.deepEqual(model.searchedPlace, WeatherSavedPlace.place(town))
    assert.deepEqual(model.savedPlaces.map(WeatherSavedPlace.id), [WeatherSavedPlace.id(town)])
    assert.equal(model.stationToOpen, null)
    assert.equal(model.pending.length, 0)
    assert.equal(model.inFlight.length, 0)
    assert.equal(model.pendingPlaceAction, null)

    model.pendingPlaceAction = WeatherPlacePickerAction.currentLocation
    assert.equal(model.applyPendingPlaceAction({ isLocationAuthorized: false }), true)
  })

  // MARK: - One build per page (docs/MESHWX_UI.md §13)

  // The state storm reports and rainfall ask about is one page's answer to "which state". It
  // was one value for the whole visit, so picking Texas on one page sent `>storm TX` from every
  // page — including one in Puerto Rico.
  it("a state picked on one page is that page's alone", () => {
    const { model } = F.makeModel()
    const dallas = 'at:32.777,-96.797'
    model.setReportState('TX', { forPageID: dallas })
    assert.equal(model.reportState({ for: dallas }), 'TX')
    assert.equal(model.reportState({ for: WeatherPage.myLocationID }), null)
    model.setReportState('PR', { forPageID: WeatherPage.myLocationID })
    assert.equal(model.reportState({ for: dallas }), 'TX')
    assert.equal(model.reportState({ for: WeatherPage.myLocationID }), 'PR')
  })

  // The swipe window: the title already names the new place while its build is still coming.
  // A page with no build has no plan, so Update is disabled and a pull sends nothing — rather
  // than sending the previous place's requests under this page's name.
  it('a page with no build has no plan, no screen and no update run', () => {
    const { model } = F.makeModel()
    assert.equal(model.screen({ for: WeatherPage.myLocationID }), null)
    assert.equal(model.build({ for: WeatherPage.myLocationID }), null)
    assert.equal(WeatherUpdatePlan.isEmpty(model.plan({ for: WeatherPage.myLocationID })), true)
    assert.equal(WeatherUpdatePlan.isEmpty(model.plan({ for: 'at:32.777,-96.797' })), true)
    assert.equal(model.isUpdating({ pageID: WeatherPage.myLocationID }), false)
    assert.equal(model.updateRequests({ pageID: WeatherPage.myLocationID }).length, 0)
    assert.equal(model.updateStatusText({ pageID: WeatherPage.myLocationID }), null)
    // A tap in that window starts nothing at all.
    model.update(model.plan({ for: WeatherPage.myLocationID }), { pageID: WeatherPage.myLocationID })
    assert.equal(model.isUpdating({ pageID: WeatherPage.myLocationID }), false)
  })

  // MARK: - Revision 10: the alert map, the traffic log, the request list

  /**
   * The screens read these and assemble nothing themselves, which is how a page came to draw one
   * place's map under another place's name. Each is one pure rule called with this page's build
   * and this model's clock.
   */
  it('a page with no build has an empty map, an empty offer list and the default selection', () => {
    const { model } = F.makeModel()
    const picture = model.alertMapPicture({ pageID: WeatherPage.myLocationID })
    assert.deepStrictEqual(picture.parts, [])
    assert.equal(picture.coversWholeCountry, false)
    assert.deepStrictEqual(model.sweepPartsOffers({ pageID: WeatherPage.myLocationID }), {})
    assert.deepStrictEqual(model.areaSelection({ pageID: WeatherPage.myLocationID }), {
      isWholeCountry: true, states: [],
    })
    assert.equal(
      WeatherRequest.wireText(model.areaSweepRequest({ includesAdvisories: false })), '>wmap',
    )
    // With nothing held the country is the measured figure: four packets, seven with advisories.
    assert.equal(model.areaSweepCost({ includesAdvisories: false }), 4)
    assert.equal(model.areaSweepCost({ includesAdvisories: true }), 7)
  })

  it('the area selection is kept on the device and is what the ask button sends', () => {
    const { model } = F.makeModel()
    model.setAreaSelection(WeatherAreaSelection.make({ states: ['tx', 'ok'] }))
    assert.deepStrictEqual(model.areaSelection(), { isWholeCountry: false, states: ['OK', 'TX'] })
    assert.equal(
      WeatherRequest.wireText(model.areaSweepRequest({ includesAdvisories: true })), '>wmap all OKTX',
    )
    // One packet per four states with nothing held, at least one.
    assert.equal(model.areaSweepCost({ includesAdvisories: false }), 1)
    // And it survives a new model over the same defaults, which is what "per device" means.
    const again = F.makeModel({ defaults: model.defaults }).model
    assert.deepStrictEqual(again.areaSelection(), { isWholeCountry: false, states: ['OK', 'TX'] })
  })

  /** Owner, 20 September 2026: *"Your requests is way too long of a list."* */
  it('the request list is split into the newest three and the whole thing', () => {
    const { model } = F.makeModel()
    for (let index = 0; index < 6; index += 1) {
      model.recordRequest({
        id: `r${index}`,
        request: WeatherRequest.digest,
        botID,
        at: F.now - index * 60_000,
      })
    }
    const split = model.requestLogSplit
    assert.equal(split.all.length, 6)
    assert.deepStrictEqual(split.newest.map((one) => one.id), ['r0', 'r1', 'r2'])
  })

  it('the traffic list is empty with no service, and carries a summary per row otherwise', () => {
    const { model, host } = F.makeModel()
    assert.deepStrictEqual(model.trafficEntries(), [])

    const log = new WeatherTrafficLog()
    host.weatherService = { trafficLog: log }
    log.record(WeatherTrafficEntry.fromDatagram({
      id: 'a',
      at: F.now,
      datagram: {
        channelIndex: 3,
        pathLength: 2,
        dataType: 0xff10,
        data: MeshWXEncoder.request({
          seq: 1,
          bot: botID,
          senderPrefix: Uint8Array.from([0x0a, 0x1b, 0x2c, 3, 4, 5]),
          timestamp: 1_789_660_000,
          text: '>wmap OKTX',
        }),
        snr: 6,
      },
    }))
    const rows = model.trafficEntries()
    assert.equal(rows.length, 1)
    assert.deepStrictEqual(rows[0].summary, { title: 'Request from 0A1B2C', detail: '>wmap OKTX' })
    model.clearTraffic()
    assert.deepStrictEqual(model.trafficEntries(), [])
  })

  // A selection pointing at a page that is gone is a page nothing can be built for, and a
  // spinner on every page until the next swipe. It is resolved and written back.
  it('a page id nothing answers to leaves the pager on my location', () => {
    const { model } = F.makeModel()
    model.showPage('at:32.777,-96.797')
    assert.equal(model.selectedPageID, WeatherPage.myLocationID)
    assert.deepEqual(model.pages.map(WeatherPage.id), [WeatherPage.myLocationID])
  })
})

/** What Places finds, and what picking it keeps — the two cases that are the model's. */
describe('Weather place search', () => {
  // **An airport code opens the station screen and nothing else** (docs/MESHWX_UI.md §3.1 U-3).
  //
  // It used to do three things at once: push the station, save a place and add a page. KAUS
  // produced a second page called "Austin" beside the one already there; TJSJ produced a page
  // called "Eleanor Roosevelt", named after the town nearest the airport.
  it('an airport code opens its station and creates no place', () => {
    const index = MeshWXTables.shared.stationIndex({ forICAO: 'TJSJ' })
    assert.notEqual(index, null)
    const { model } = F.makeModel()

    model.pendingPlaceAction = WeatherPlacePickerAction.station({ index })
    assert.equal(model.applyPendingPlaceAction({ isLocationAuthorized: false }), false)
    assert.equal(model.stationToOpen?.index, index)
    assert.equal(model.savedPlaces.length, 0)
    assert.deepEqual(model.pages.map(WeatherPage.id), [WeatherPage.myLocationID])
    // And it is pushed over the page the user was already on.
    assert.equal(model.stationToOpen?.pageID, model.selectedPageID)
  })

  // Each destination is judged against the page it was opened from, not the page the pager has
  // since landed on (docs/MESHWX_UI.md §3.1 U-18).
  it('a tapped alert carries the page it was raised for', () => {
    const { model } = F.makeModel()
    const identity = { event: 0, office: 1, etn: 42 }
    model.alertToOpen = WeatherAlertTarget.make({ pageID: 'at:30.510,-97.679', identity })
    assert.equal(model.alertToOpen?.pageID, 'at:30.510,-97.679')
    assert.deepEqual(model.alertToOpen?.identity, identity)
  })
})

/** When the map outlines are worth loading (docs/MESHWX_UI.md §3.1 U-27). */
describe('Weather outlines', () => {
  /** A warning the phone must draw itself: area runs, no polygon of its own. */
  function stateWithUndrawnWarning() {
    const one = F.warning({
      event: 3,
      office: 35,
      etn: 42,
      expiresMinutes: 29_000_000,
      areas: [{ state: 42, county: true, start: 100, run: 1 }],
    })
    const state = WeatherBotState.make({ botID })
    state.warnings['3.35.42'] = WeatherStoredWarning.make({ warning: one, receivedAt: F.now })
    return { [String(botID)]: state }
  }

  // The San Juan case: a place, nothing held for it. The outlines are what say the place is
  // outside the radio's area and name the zone and county to ask by, so without them the page
  // asked for no alerts at all and no alert could ever arrive to load them.
  it('a place needs the outlines even with nothing held', () => {
    assert.equal(WeatherScreenBuilder.needsGeometry({ hasPlace: true, states: {} }), true)
    assert.equal(
      WeatherScreenBuilder.needsGeometry({ hasPlace: true, states: stateWithUndrawnWarning() }),
      true,
    )
  })

  // No place: only a warning that must be drawn is worth 15 MB of outlines.
  it('with no place only an undrawn warning asks for them', () => {
    assert.equal(WeatherScreenBuilder.needsGeometry({ hasPlace: false, states: {} }), false)
    assert.equal(
      WeatherScreenBuilder.needsGeometry({
        hasPlace: false,
        states: { [String(botID)]: WeatherBotState.make({ botID }) },
      }),
      false,
    )
    assert.equal(
      WeatherScreenBuilder.needsGeometry({ hasPlace: false, states: stateWithUndrawnWarning() }),
      true,
    )
  })

  // San Juan lies in two land zones and in Atlantic marine zone AMZ712, in whatever order the
  // outlines answer. The land zone of the place's own state is the one a radio is asked about.
  it('a coastal place asks about its land zone, not the water', () => {
    const sanJuan = ['AMZ712', 'PRZ016', 'PRC127', 'PRZ001']
    assert.equal(
      WeatherScreenBuilder.placeZone({ from: sanJuan, stateCode: 'PR', county: 'PRC127' }),
      'PRZ001',
    )
    // No state code held yet: the county names the state.
    assert.equal(
      WeatherScreenBuilder.placeZone({ from: sanJuan, stateCode: null, county: 'PRC127' }),
      'PRZ001',
    )
    // Nothing says which state: any zone beats no zone, and the choice is at least stable.
    assert.equal(WeatherScreenBuilder.placeZone({ from: sanJuan, stateCode: null, county: null }), 'AMZ712')
    assert.equal(
      WeatherScreenBuilder.placeZone({ from: ['TXC453', 'TXZ192'], stateCode: 'TX', county: 'TXC453' }),
      'TXZ192',
    )
    assert.equal(
      WeatherScreenBuilder.placeZone({ from: ['TXC453'], stateCode: 'TX', county: 'TXC453' }),
      null,
    )
  })
})
