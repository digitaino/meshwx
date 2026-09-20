// Port of MC1Services/Tests/MC1ServicesTests/Weather/Screen/WeatherPagedScreenTests.swift
//
// The tool is a pager of places, and everything it shows belongs to **one** of them
// (docs/MESHWX_UI.md §4, §13).
//
// The bug these are written against: one snapshot and one context for the whole visit, with every
// screen reached from a page reading them. Swipe to Round Rock, open the forecast discussion, and
// Austin's discussion was on it — same subject, wrong place, and the blurb above it naming Round
// Rock's office while the header named Austin's.

import test from 'node:test'
import assert from 'node:assert/strict'

import { MeshWXTextSubject } from '../src/meshwx/index.js'
import {
  WeatherAlertNotificationTapTarget,
  WeatherBot,
  WeatherRequest,
  WeatherSessionInfo,
} from '../src/weather/index.js'
import {
  WeatherPage,
  WeatherPages,
  WeatherReportSelection,
  WeatherSavedPlace,
  WeatherSavedPlaces,
  WeatherScreenSnapshot,
  WeatherTextItem,
  WeatherUpdatePlan,
  WeatherUpdateRuns,
  WeatherWatchedPlace,
} from '../src/screen/index.js'
import { loadedGeometry, loadGeometry, loadTables, WeatherPhoneFixture as P } from './helpers/screen-fixture.js'

const tables = await loadTables()
await loadGeometry()
const geometry = loadedGeometry()

const wxAus = WeatherBot.make({
  publicKey: new Uint8Array([0x1d, 0x04, ...new Array(30).fill(0x55)]),
  name: 'WX-AUS',
  latitude: 0,
  longitude: 0,
  lastAdvert: null,
})

function snapshot({ place, pageID, state = P.state(), now = P.now }) {
  return WeatherScreenSnapshot.make(
    WeatherScreenSnapshot.Inputs.make({
      states: { [String(P.botID)]: state },
      bots: [wxAus],
      preferredBotID: null,
      place,
      pageID,
      isRadioConnected: true,
      firmwareSupportsWeather: true,
      firmwareVersion: 'v1.15.0',
      hasWeatherChannel: true,
      session: WeatherSessionInfo.make({ startedAt: now - 3600 * 1000 }),
      now,
      timeZone: P.timeZone,
    }),
    { geometry, tables },
  )
}

function saved(label, { lat, lon, watched = false } = {}) {
  return WeatherSavedPlace.make({ label, latitude: lat, longitude: lon, chosenAt: P.now, isWatched: watched })
}

// MARK: - One snapshot, one page

test("a snapshot says which page it was built for, and carries that page's place", () => {
  // The key travels inside the value: nothing downstream has to be told separately which place it
  // is looking at, and a build that lands after a swipe cannot be read as the new page's.
  const austin = P.place(P.austin)
  const dallas = P.place(P.dallas, { label: 'Dallas, TX' })
  const here = snapshot({ place: austin, pageID: WeatherPage.myLocationID })
  const savedPage = snapshot({ place: dallas, pageID: 'at:32.777,-96.797' })

  assert.equal(here.page.pageID, 'here')
  assert.deepEqual(here.page.place, austin)
  assert.equal(savedPage.page.pageID, 'at:32.777,-96.797')
  assert.deepEqual(savedPage.page.place, dallas)
  // Same held state, two pages: the snapshots differ by the place they answer for, and each says
  // which page that is.
  assert.notDeepEqual(here.page, savedPage.page)
  assert.notDeepEqual(here.place, savedPage.place)
})

test("the plan for one page is never the other page's", () => {
  // Update plans one page's requests from that page's own build. Austin's forecast point is not
  // Dallas's, and during a swipe the button must not send the one under the other's name.
  const state = P.state()
  const plan = (place, pageID, county) =>
    WeatherUpdatePlan.make({
      snapshot: snapshot({ place, pageID, state }),
      sourceState: state,
      placeCountyUGC: county,
      placeOffice: null,
      coverageAlreadyAsked: true,
      tables,
      now: P.now,
    })
  const austin = plan(P.place(P.austin), 'here', 'TXC453')
  const dallas = plan(P.place(P.dallas, { label: 'Dallas, TX' }), 'at:32.777,-96.797', 'TXC113')

  assert.notDeepEqual(austin, dallas)
  // Austin holds a current forecast for its own point; Dallas holds none, so its plan asks for one
  // — and for Dallas's point, never Austin's.
  const austinForecast = austin.steps.find((step) => step.item === 'forecast')?.request ?? null
  const dallasForecast = dallas.steps.find((step) => step.item === 'forecast')?.request ?? null
  assert.notEqual(dallasForecast, null)
  assert.notDeepEqual(austinForecast, dallasForecast)
})

// MARK: - Text reports (§12, §14 Q5)

function text(subject, { group, request, body, minutesAgo = 0 }) {
  return WeatherTextItem.make({
    botID: P.botID,
    assembly: {
      subject,
      group,
      total: 1,
      chunks: { 0: body },
      firstReceivedAt: P.now - minutesAgo * 60 * 1000,
      lastReceivedAt: P.now - minutesAgo * 60 * 1000,
      request,
      source: 0,
      wasCut: false,
    },
  })
}

test("a discussion answered for another office is not this page's", () => {
  // The owner's bug, in one assertion: a discussion this phone asked Fort Worth for is not the
  // discussion for a page whose office is Austin/San Antonio — however new it is.
  const fortWorth = text(MeshWXTextSubject.forecastDiscussion, {
    group: 1,
    request: WeatherRequest.forecastDiscussion({ office: 'FWD' }),
    body: 'FWD AFD',
  })
  const austin = text(MeshWXTextSubject.forecastDiscussion, {
    group: 2,
    request: WeatherRequest.forecastDiscussion({ office: 'EWX' }),
    body: 'EWX AFD',
    minutesAgo: 90,
  })

  const chosen = WeatherReportSelection.choose({
    texts: [fortWorth, austin],
    subject: MeshWXTextSubject.forecastDiscussion,
    request: WeatherRequest.forecastDiscussion({ office: 'EWX' }),
    isByArea: true,
  })
  assert.deepEqual(chosen.item.assembly.request, WeatherRequest.forecastDiscussion({ office: 'EWX' }))
  assert.equal(chosen.isOwn, true)

  // With nothing held for this office, the other office's reply is not borrowed: the screen says
  // nothing rather than something about somewhere else.
  assert.equal(
    WeatherReportSelection.choose({
      texts: [fortWorth],
      subject: MeshWXTextSubject.forecastDiscussion,
      request: WeatherRequest.forecastDiscussion({ office: 'EWX' }),
      isByArea: true,
    }),
    null,
  )
})

test("an overheard reply is shown as somebody else's, for an area this phone can't name", () => {
  const overheard = text(MeshWXTextSubject.stormReports, { group: 3, request: null, body: 'SPOTTER REPORTS' })
  const chosen = WeatherReportSelection.choose({
    texts: [overheard],
    subject: MeshWXTextSubject.stormReports,
    request: WeatherRequest.stormReports({ state: 'PR' }),
    isByArea: true,
  })
  assert.equal(chosen.isOwn, false)
  assert.equal(chosen.isUnknownArea, true)

  // `>hwo` and `>space` take no place: an overheard one is the same product this page would have
  // asked for, so there is no unknown area to admit to.
  const outlook = text(MeshWXTextSubject.hazardousOutlook, { group: 4, request: null, body: 'HWO' })
  const chosenOutlook = WeatherReportSelection.choose({
    texts: [outlook],
    subject: MeshWXTextSubject.hazardousOutlook,
    request: WeatherRequest.hazardousOutlook,
    isByArea: false,
  })
  assert.equal(chosenOutlook.isOwn, false)
  assert.equal(chosenOutlook.isUnknownArea, false)
})

test("a state a page asked about does not answer another page's request", () => {
  // The state override was one visit-wide value: picking Texas on one page sent `>storm TX` for
  // every page. Per page, one page's answer cannot be shown on another's.
  const texas = text(MeshWXTextSubject.stormReports, {
    group: 5,
    request: WeatherRequest.stormReports({ state: 'TX' }),
    body: 'TX STORM REPORTS',
  })
  assert.equal(
    WeatherReportSelection.choose({
      texts: [texas],
      subject: MeshWXTextSubject.stormReports,
      request: WeatherRequest.stormReports({ state: 'TX' }),
      isByArea: true,
    })?.isOwn,
    true,
  )
  assert.equal(
    WeatherReportSelection.choose({
      texts: [texas],
      subject: MeshWXTextSubject.stormReports,
      request: WeatherRequest.stormReports({ state: 'PR' }),
      isByArea: true,
    }),
    null,
  )
  // A page with no place to build a request from can still be shown what the channel carried, and
  // only ever as somebody else's.
  const overheard = text(MeshWXTextSubject.stormReports, { group: 6, request: null, body: 'SOMEWHERE' })
  assert.equal(
    WeatherReportSelection.choose({
      texts: [texas, overheard],
      subject: MeshWXTextSubject.stormReports,
      request: null,
      isByArea: true,
    })?.item.assembly.group,
    6,
  )
})

// MARK: - The pages under the pager (§5, §16)

test('turning on a bell keeps every page', () => {
  // A bell adds no row, so it trims none. It used to run the list through the ceiling, which could
  // take a page out from under the pager — including the page the tap came from.
  const places = Array.from({ length: 13 }, (_, index) => saved(`Town ${index}`, { lat: 30 + index / 10, lon: -97 }))
  const watched = WeatherSavedPlaces.setting({ watched: true, id: WeatherSavedPlace.id(places[3]), in: places })
  assert.equal(watched.length, places.length)
  assert.deepEqual(watched.map(WeatherSavedPlace.id), places.map(WeatherSavedPlace.id))
  assert.ok(watched[3].isWatched)
  // And off again, with nothing else touched.
  const off = WeatherSavedPlaces.setting({ watched: false, id: WeatherSavedPlace.id(places[3]), in: watched })
  assert.deepEqual(off, places)
})

test('the ceiling never drops a watched place', () => {
  const places = Array.from({ length: 20 }, (_, index) => saved(`Town ${index}`, { lat: 30 + index / 10, lon: -97 }))
  places[19].isWatched = true
  const ordered = WeatherSavedPlaces.ordered(places)
  assert.ok(ordered.some((place) => WeatherSavedPlace.id(place) === WeatherSavedPlace.id(places[19])))
  assert.equal(ordered.length, WeatherSavedPlaces.limit)
})

test('a selection naming a page that is gone resolves to the first one', () => {
  // The selection is resolved and **written back**, so `selectedPageID` never names a page that is
  // not there — which was a snapshot nothing could be built for, and a spinner that never ended.
  const austin = saved('Austin, TX', { lat: 30.27, lon: -97.74 })
  const pages = WeatherPages.make({ saved: [austin] })
  const id = WeatherSavedPlace.id(austin)
  assert.equal(WeatherPages.selection(id, { in: pages }), id)
  assert.equal(WeatherPages.selection(id, { in: WeatherPages.make({ saved: [] }) }), WeatherPage.myLocationID)
  assert.equal(WeatherPages.selection('at:0.000,0.000', { in: pages }), WeatherPage.myLocationID)
})

test("a page's neighbours are the pages either side of it", () => {
  const list = [saved('A', { lat: 30, lon: -97 }), saved('B', { lat: 31, lon: -97 }), saved('C', { lat: 32, lon: -97 })]
  const pages = WeatherPages.make({ saved: list })
  const id = (index) => WeatherSavedPlace.id(list[index])
  assert.deepEqual(WeatherPages.neighbours({ of: WeatherPage.myLocationID, in: pages }), [id(0)])
  assert.deepEqual(WeatherPages.neighbours({ of: id(0), in: pages }), [WeatherPage.myLocationID, id(1)])
  assert.deepEqual(WeatherPages.neighbours({ of: id(2), in: pages }), [id(1)])
  assert.deepEqual(WeatherPages.neighbours({ of: 'at:0.000,0.000', in: pages }), [])
})

test("a notification's watched place names the page it belongs to", () => {
  // A notification is raised for one watched place and says so. The tap opens that place's page
  // first, so "Covers Austin" is not computed for Dallas.
  const dallas = saved('Dallas, TX', { lat: 32.7767, lon: -96.797, watched: true })
  const dallasID = WeatherSavedPlace.id(dallas)
  assert.equal(WeatherPages.pageID({ forWatchedPlaceID: dallasID }), dallasID)
  assert.equal(
    WeatherPages.pageID({ forWatchedPlaceID: WeatherWatchedPlace.myLocationID }),
    WeatherPage.myLocationID,
  )

  const target = WeatherAlertNotificationTapTarget.make({
    identity: { event: 3, office: 35, etn: 42 },
    botID: P.botID,
    placeID: dallasID,
    tappedAt: P.now,
  })
  const pages = WeatherPages.make({ saved: [saved('Austin, TX', { lat: 30.27, lon: -97.74 }), dallas] })
  const pageID = WeatherPages.pageID({ forWatchedPlaceID: target.placeID })
  assert.equal(WeatherPages.selection(pageID, { in: pages }), dallasID)
})

// MARK: - Update runs (§11.1)

test('an update run belongs to the page that started it, and only one runs at a time', () => {
  // Per page, so one place's run does not spin another place's button; one at a time, so a pull
  // and a tap cannot both be running and the loser's cleanup cannot put the winner's spinner out.
  let runs = WeatherUpdateRuns.make()
  assert.ok(!WeatherUpdateRuns.isRunning(runs))

  const first = WeatherUpdateRuns.begin(runs, {
    pageID: 'here',
    requests: [WeatherRequest.digest, WeatherRequest.observations],
  })
  runs = first.runs
  assert.ok(first.started)
  assert.ok(WeatherUpdateRuns.isRunning(runs, { pageID: 'here' }))
  assert.ok(!WeatherUpdateRuns.isRunning(runs, { pageID: 'dallas' }))
  assert.deepEqual(WeatherUpdateRuns.requests(runs, { pageID: 'here' }), [WeatherRequest.digest, WeatherRequest.observations])
  assert.deepEqual(WeatherUpdateRuns.requests(runs, { pageID: 'dallas' }), [])

  // A second run anywhere is refused rather than replacing the first.
  const otherPage = WeatherUpdateRuns.begin(runs, { pageID: 'dallas', requests: [WeatherRequest.digest] })
  const samePage = WeatherUpdateRuns.begin(runs, { pageID: 'here', requests: [WeatherRequest.digest] })
  assert.ok(!otherPage.started)
  assert.ok(!samePage.started)
  assert.ok(WeatherUpdateRuns.isRunning(runs, { pageID: 'here' }))

  // A run that is not the one going ends nothing.
  runs = WeatherUpdateRuns.end(runs, { pageID: 'dallas' })
  assert.ok(WeatherUpdateRuns.isRunning(runs, { pageID: 'here' }))
  runs = WeatherUpdateRuns.end(runs, { pageID: 'here' })
  assert.ok(!WeatherUpdateRuns.isRunning(runs))
  // What it asked for is kept, so the caption can still say how it went.
  assert.deepEqual(WeatherUpdateRuns.requests(runs, { pageID: 'here' }), [WeatherRequest.digest, WeatherRequest.observations])
  assert.ok(WeatherUpdateRuns.begin(runs, { pageID: 'dallas', requests: [WeatherRequest.digest] }).started)
})

test('an empty plan starts no run', () => {
  // A page with no build has no plan, which is what keeps the button and the pull inert in the
  // swipe window.
  const runs = WeatherUpdateRuns.make()
  const started = WeatherUpdateRuns.begin(runs, {
    pageID: 'here',
    requests: WeatherUpdatePlan.requests(WeatherUpdatePlan.empty),
  })
  assert.ok(!started.started)
  assert.ok(!WeatherUpdateRuns.isRunning(started.runs))
})
