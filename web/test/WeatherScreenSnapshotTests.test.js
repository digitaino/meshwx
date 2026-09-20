// Port of MC1Services/Tests/MC1ServicesTests/Weather/Screen/WeatherScreenSnapshotTests.swift

import test from 'node:test'
import assert from 'node:assert/strict'

import { MeshWXTextSubject } from '../src/meshwx/index.js'
import {
  WeatherBot,
  WeatherPendingRequest,
  WeatherRequest,
  WeatherRequestOutcome,
  WeatherSessionInfo,
  WeatherTransportLink,
} from '../src/weather/index.js'
import {
  WeatherPrimaryStation,
  WeatherRequestBlock,
  WeatherRequestStatus,
  WeatherScreenBanner,
  WeatherScreenSnapshot,
  WeatherSettledOutcome,
} from '../src/screen/index.js'
import { loadedGeometry, loadGeometry, loadTables, WeatherPhoneFixture as P } from './helpers/screen-fixture.js'

const tables = await loadTables()
await loadGeometry()
const geometry = loadedGeometry()

/** WX-AUS as the owner's radio holds it: key prefix 1D 04, advert position 0,0. */
const wxAus = WeatherBot.make({
  publicKey: new Uint8Array([0x1d, 0x04, ...new Array(30).fill(0x55)]),
  name: 'WX-AUS',
  latitude: 0,
  longitude: 0,
  lastAdvert: null,
})

function inputs({
  states = null,
  bots = null,
  place = P.place(P.austin),
  connected = true,
  link = null,
  firmware = true,
  channel = true,
  session = WeatherSessionInfo.make({ startedAt: P.now - 3600 * 1000 }),
} = {}) {
  return WeatherScreenSnapshot.Inputs.make({
    states: states ?? P.states(),
    bots: bots ?? [wxAus],
    preferredBotID: null,
    place,
    isRadioConnected: connected,
    transportLink: link,
    firmwareSupportsWeather: firmware,
    firmwareVersion: 'v1.14.0',
    hasWeatherChannel: channel,
    session,
    now: P.now,
    timeZone: P.timeZone,
  })
}

function snapshot(built) {
  return WeatherScreenSnapshot.make(built, { geometry, tables })
}

test("the owner's phone at 23:20 reads as it should", () => {
  assert.equal(WeatherBot.botID(wxAus), P.botID)
  const screen = snapshot(inputs())
  assert.equal(screen.banner, null)
  assert.equal(screen.source?.botID, P.botID)
  assert.equal(screen.source?.bot?.name, 'WX-AUS')
  assert.equal(screen.requestBlock, null)
  assert.deepEqual(screen.alertStatus, { kind: 'notChecked' })
  assert.equal(screen.alerts.length, 0)
  assert.equal(screen.primaryStation.kind, 'reading')
  assert.equal(screen.primaryStation.value.station.icao, 'KATT')
  assert.equal(screen.forecast.kind, 'forecast')
  assert.equal(screen.forecast.value.point.index, 103)
  assert.deepEqual(screen.otherPlaces.map((one) => one.point.index), [304, 1010])
  assert.equal(screen.readings.length, 14)
})

test('offline, requests are blocked but the picture stays', () => {
  const screen = snapshot(inputs({ connected: false }))
  assert.equal(screen.requestBlock, WeatherRequestBlock.radioOffline)
  assert.equal(screen.banner, null)
  assert.equal(screen.readings.length, 14)
})

test('a transport with its own link stands in for the radio and announces its bot', () => {
  // The bot bridge is a transport with a link of its own: no radio, no advert, no contact, and
  // the tool can still ask (`WeatherTransportLink`).
  const screen = snapshot(inputs({ bots: [], connected: false, link: WeatherTransportLink.up({ bot: wxAus }) }))
  assert.equal(screen.requestBlock, null)
  assert.equal(screen.source?.botID, P.botID)
  assert.equal(screen.source?.bot?.name, 'WX-AUS')
  assert.deepEqual(screen.source?.bot?.publicKey, wxAus.publicKey)
  assert.ok(screen.knownBotIDs.includes(P.botID))
  assert.equal(screen.banner, null)
})

test('a transport with its own link answers for the firmware too', () => {
  // §3.1 U-20: the firmware claim is about the transport the request goes out on.
  const screen = snapshot(
    inputs({ bots: [], connected: false, link: WeatherTransportLink.up({ bot: wxAus }), firmware: false }),
  )
  assert.equal(screen.requestBlock, null)
  assert.equal(screen.banner, null)
  // Never heard of a radio at all: the bridge still asks.
  assert.equal(
    snapshot(inputs({ bots: [], connected: false, link: WeatherTransportLink.up({ bot: wxAus }), firmware: null }))
      .requestBlock,
    null,
  )
  // Over a radio the old firmware is still the block it always was.
  assert.equal(snapshot(inputs({ firmware: false })).requestBlock, WeatherRequestBlock.firmwareTooOld)
})

test("the link's bot never displaces the contact for the same bot", () => {
  const advertised = WeatherBot.make({
    publicKey: wxAus.publicKey,
    name: 'WX-AUS',
    latitude: 30.27,
    longitude: -97.74,
    lastAdvert: P.now - 600 * 1000,
  })
  const screen = snapshot(
    inputs({ bots: [advertised], connected: false, link: WeatherTransportLink.up({ bot: wxAus }) }),
  )
  assert.equal(screen.requestBlock, null)
  assert.equal(screen.source?.bot?.lastAdvert, advertised.lastAdvert)
  assert.deepEqual(screen.knownBotIDs, [P.botID])
})

test('with no link of its own the radio still decides', () => {
  // Every build over a radio: the link is null and nothing about the block changes.
  assert.equal(snapshot(inputs({ connected: false, link: null })).requestBlock, WeatherRequestBlock.radioOffline)
  // Heard on the channel, no contact for it: still the block it always was.
  assert.equal(snapshot(inputs({ bots: [], connected: true, link: null })).requestBlock, WeatherRequestBlock.botNotAnnounced)
  assert.equal(snapshot(inputs({ states: {}, bots: [], connected: true, link: null })).requestBlock, WeatherRequestBlock.noBot)
})

test('a bot with no advert can be read but not asked', () => {
  const screen = snapshot(inputs({ bots: [] }))
  assert.equal(screen.source?.botID, P.botID)
  assert.equal(screen.source?.bot, null)
  assert.equal(screen.requestBlock, WeatherRequestBlock.botNotAnnounced)
})

test('old firmware is the banner and the block', () => {
  const screen = snapshot(inputs({ firmware: false }))
  assert.deepEqual(screen.banner, WeatherScreenBanner.firmwareTooOld({ version: 'v1.14.0' }))
  assert.equal(screen.requestBlock, WeatherRequestBlock.firmwareTooOld)
})

test('a missing channel is a banner only until weather arrives on the radio', () => {
  assert.deepEqual(snapshot(inputs({ channel: false })).banner, WeatherScreenBanner.channelMissing)
  assert.equal(snapshot(inputs({ channel: false })).requestBlock, WeatherRequestBlock.channelMissing)
  const arriving = WeatherSessionInfo.make({
    startedAt: P.now - 600 * 1000,
    lastChannelDatagramAt: P.now - 60 * 1000,
  })
  assert.equal(snapshot(inputs({ channel: false, session: arriving })).banner, null)
})

test('nothing heard and no bots is its own banner', () => {
  const screen = snapshot(inputs({ states: {}, bots: [] }))
  assert.deepEqual(screen.banner, WeatherScreenBanner.noBotHeard)
  assert.equal(screen.requestBlock, WeatherRequestBlock.noBot)
  assert.deepEqual(screen.primaryStation, WeatherPrimaryStation.noObservations)
})

test('a bot quiet for two hours is flagged', () => {
  const state = P.state()
  state.lastHeardAt = P.now - 2 * 3600 * 1000
  state.lastLiveHeardAt = P.now - 2 * 3600 * 1000
  assert.equal(snapshot(inputs({ states: { [String(P.botID)]: state } })).sourceQuietSince, P.now - 2 * 3600 * 1000)
  const recent = P.state()
  recent.lastLiveHeardAt = P.now - 60 * 1000
  assert.equal(snapshot(inputs({ states: { [String(P.botID)]: recent } })).sourceQuietSince, null)
})

test('a backlog drained just now does not count as hearing the bot', () => {
  // A backlog drained at connect is stamped with the drain time: it cannot say the bot is in
  // range.
  const state = P.state()
  state.lastHeardAt = P.now - 30 * 1000
  state.lastLiveHeardAt = P.now - 3 * 3600 * 1000
  const screen = snapshot(inputs({ states: { [String(P.botID)]: state } }))
  assert.equal(screen.source?.lastHeardAt, P.now - 30 * 1000)
  assert.equal(screen.sourceQuietSince, P.now - 3 * 3600 * 1000)
})

test("a text somebody else asked for is not this phone's", () => {
  const state = P.state()
  state.texts['240'] = {
    subject: MeshWXTextSubject.spaceWeather,
    group: 240,
    total: 1,
    chunks: { 0: 'Kp 4' },
    firstReceivedAt: P.now,
    lastReceivedAt: P.now,
    request: null,
    source: 0,
    wasCut: false,
  }
  const screen = snapshot(inputs({ states: { [String(P.botID)]: state } }))
  assert.equal(screen.texts.length, 1)
  assert.equal(screen.texts[0].assembly.request, null)
})

// MARK: - Weather request status

const bot = WeatherBot.make({
  publicKey: new Uint8Array([0x7a, 0x4c, ...new Array(30).fill(0x11)]),
  name: 'WX-AUS',
  latitude: 30.27,
  longitude: -97.74,
  lastAdvert: null,
})

function pending(request, { attempt = 0 } = {}) {
  return WeatherPendingRequest.make({
    request,
    botID: WeatherBot.botID(bot),
    botPublicKey: bot.publicKey,
    sentAt: P.now,
    attempt,
  })
}

test('a request on the air shows as pending even if the radio then drops', () => {
  assert.deepEqual(
    WeatherRequestStatus.resolve({
      request: WeatherRequest.digest,
      block: WeatherRequestBlock.radioOffline,
      pending: [pending(WeatherRequest.digest, { attempt: 1 })],
      outcomes: {},
      now: P.now,
    }),
    WeatherRequestStatus.pending({ attempt: 1, sentAt: P.now }),
  )
})

test('a block wins over waiting and over old outcomes', () => {
  assert.deepEqual(
    WeatherRequestStatus.resolve({
      request: WeatherRequest.digest,
      block: WeatherRequestBlock.channelMissing,
      pending: [pending(WeatherRequest.observations)],
      outcomes: {},
      now: P.now,
    }),
    WeatherRequestStatus.blocked(WeatherRequestBlock.channelMissing),
  )
})

test('one at a time, and an outcome stays for five minutes', () => {
  assert.deepEqual(
    WeatherRequestStatus.resolve({
      request: WeatherRequest.digest,
      block: null,
      pending: [pending(WeatherRequest.observations)],
      outcomes: {},
      now: P.now,
    }),
    WeatherRequestStatus.waitingForOther,
  )
  const timedOut = WeatherRequestOutcome.timedOut({ botWasHeard: false })
  const outcomes = {
    [WeatherRequest.wireText(WeatherRequest.digest)]: WeatherSettledOutcome.make({
      outcome: timedOut,
      at: P.now - 60 * 1000,
    }),
  }
  assert.deepEqual(
    WeatherRequestStatus.resolve({ request: WeatherRequest.digest, block: null, pending: [], outcomes, now: P.now }),
    WeatherRequestStatus.settled(timedOut, { at: P.now - 60 * 1000 }),
  )
  assert.deepEqual(
    WeatherRequestStatus.resolve({
      request: WeatherRequest.digest,
      block: null,
      pending: [],
      outcomes,
      now: P.now + 300 * 1000,
    }),
    WeatherRequestStatus.idle,
  )
})
