// The session against `FakeRadioTransport`: the handshake, the command timeout, push
// routing while a command is in flight, the connect drain and `isDrainingBacklog`, a DM with
// its ACK. The Swift cases these follow are in
// `MeshCore/Tests/MeshCoreTests/Session/` (`MeshCoreSessionCommandCorrelationTests`,
// `GetMessageTimeoutTests`, `AutoMessageFetchTests`).

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  FakeRadioTransport,
  MeshCoreSession,
  SessionConfiguration,
  makeFakeContact,
  utf8Encode,
} from '../src/radio/index.js'

const RADIO_KEY = Uint8Array.from(Array.from({ length: 32 }, (_, i) => (i + 1) & 0xff))

function makeSession(transportOptions = {}, sessionOptions = {}) {
  const transport = new FakeRadioTransport({ publicKey: RADIO_KEY, ...transportOptions })
  const session = new MeshCoreSession(transport, sessionOptions)
  return { transport, session }
}

test('the connect handshake reports self info, device info and datagram support', async () => {
  const { session } = makeSession({ firmwareVersion: 11, name: 'Owner radio', model: 'Heltec V3' })

  await session.start()

  assert.equal(session.state, 'connected')
  assert.deepEqual(session.selfInfo.publicKey, RADIO_KEY)
  assert.equal(session.selfInfo.name, 'Owner radio')
  assert.equal(session.deviceInfo.firmwareVersion, 11)
  assert.equal(session.deviceInfo.model, 'Heltec V3')
  assert.equal(session.deviceInfo.maxChannels, 40)
  assert.equal(session.deviceInfo.maxContacts, 100)
  assert.equal(session.supportsChannelDatagrams, true)

  await session.stop()
  assert.equal(session.state, 'disconnected')
})

test('firmware older than v11 cannot carry channel datagrams', async () => {
  const { session } = makeSession({ firmwareVersion: 10 })
  await session.start()
  assert.equal(session.supportsChannelDatagrams, false)
  await session.stop()
})

test('the handshake sets the radio clock only when it has drifted', async () => {
  const now = 1_704_067_200_000

  // Two seconds out: inside the tolerance, so the radio is left alone.
  const inSync = makeSession({ deviceTime: now - 2000 }, { now: () => now })
  const syncedEvents = []
  inSync.session.subscribe((event) => syncedEvents.push(event.kind))
  await inSync.session.start()
  assert.equal(inSync.transport.deviceTime, now - 2000)
  assert.ok(!syncedEvents.includes('deviceTimeSynced'))
  await inSync.session.stop()

  // A minute out: corrected.
  const drifted = makeSession({ deviceTime: now - 60_000 }, { now: () => now })
  await drifted.session.start()
  assert.equal(drifted.transport.deviceTime, now)
  await drifted.session.stop()
})

test('a command that is never answered fails with a typed timeout', async () => {
  const transport = new FakeRadioTransport({ publicKey: RADIO_KEY, answerCommands: false })
  const session = new MeshCoreSession(transport, {
    configuration: SessionConfiguration.make({ defaultTimeout: 0.05 }),
  })

  await assert.rejects(
    () => session.start(),
    (error) => error.name === 'MeshCoreError' && error.kind === 'timeout',
  )
  assert.equal(session.state, 'disconnected')
})

test('a device error response fails the command with its sub-code', async () => {
  const { session } = makeSession({ maxChannels: 8 })
  await session.start()

  // Slot 40 is past this radio's last one: firmware answers ERR_CODE_NOT_FOUND.
  await assert.rejects(
    () => session.getChannel({ index: 40 }),
    (error) => error.kind === 'deviceError' && error.code === 2,
  )

  await session.stop()
})

test('pushes reach subscribers while a command is in flight and do not complete it', async () => {
  const { transport, session } = makeSession()
  await session.start()

  const seen = []
  session.subscribe((event) => seen.push(event))

  const pending = session.getChannel({ index: 3 })
  // An advert arriving inside the command's window: routed to subscribers, ignored by the
  // command, which still gets its own answer.
  transport.deliverAdvertisement({ publicKey: new Uint8Array(32).fill(0x55) })

  const info = await pending
  assert.equal(info.index, 3)
  assert.ok(seen.some((event) => event.kind === 'advertisement'))

  await session.stop()
})

test('commands are serialised: one exchange at a time', async () => {
  const { transport, session } = makeSession()
  await session.start()
  const before = transport.sentFrames.length

  const results = await Promise.all([
    session.getChannel({ index: 0 }),
    session.getChannel({ index: 1 }),
    session.getChannel({ index: 2 }),
  ])

  assert.deepEqual(
    results.map((info) => info.index),
    [0, 1, 2],
  )
  // Three commands, three frames, in the order they were asked for.
  const sent = transport.sentFrames.slice(before)
  assert.deepEqual(
    sent.map((frame) => [frame[0], frame[1]]),
    [
      [0x1f, 0],
      [0x1f, 1],
      [0x1f, 2],
    ],
  )

  await session.stop()
})

test('the connect drain empties the queue and marks every message as backlog', async () => {
  const transport = new FakeRadioTransport({ publicKey: RADIO_KEY })
  transport.deliverChannelData({
    channelIndex: 31,
    dataType: 0xff10,
    data: utf8Encode('queued'),
  })
  transport.deliverContactMessage({
    senderPublicKeyPrefix: Uint8Array.of(1, 2, 3, 4, 5, 6),
    text: 'while you were out',
  })

  const session = new MeshCoreSession(transport)
  const polled = []
  session.subscribe((event) => {
    if (['contactMessage', 'channelMessage', 'channelData'].includes(event.kind)) polled.push(event)
  })

  assert.equal(session.isDrainingBacklog, false)
  await session.start()

  assert.equal(polled.length, 2)
  assert.deepEqual(
    polled.map((event) => event.kind),
    ['channelData', 'contactMessage'],
  )
  assert.ok(polled.every((event) => event.isDrainingBacklog === true))
  assert.equal(session.isDrainingBacklog, false, 'the flag falls when the drain ends')

  await session.stop()
})

test('a messages-waiting push drains live traffic, which is not backlog', async () => {
  const { transport, session } = makeSession()
  const polled = []
  session.subscribe((event) => {
    if (['contactMessage', 'channelMessage', 'channelData'].includes(event.kind)) polled.push(event)
  })

  await session.start()
  assert.equal(polled.length, 0)

  transport.deliverChannelText({ channelIndex: 0, text: 'live one' })
  await waitFor(() => polled.length === 1)

  assert.equal(polled[0].kind, 'channelMessage')
  assert.equal(polled[0].message.text, 'live one')
  assert.equal(polled[0].isDrainingBacklog, false, 'a push-driven drain is live traffic')

  await session.stop()
})

test('every message is emitted, chat included, never silently dropped', async () => {
  const { transport, session } = makeSession()
  const kinds = []
  session.subscribe((event) => {
    if (['contactMessage', 'channelMessage', 'channelData'].includes(event.kind)) kinds.push(event.kind)
  })
  await session.start()

  transport.deliverContactMessage({
    senderPublicKeyPrefix: Uint8Array.of(9, 9, 9, 9, 9, 9),
    text: 'a DM from a friend',
  })
  transport.deliverChannelText({ channelIndex: 0, text: 'public channel chatter' })
  transport.deliverChannelData({ channelIndex: 31, dataType: 0xff10, data: utf8Encode('wx') })
  await waitFor(() => kinds.length === 3)

  assert.deepEqual(kinds, ['contactMessage', 'channelMessage', 'channelData'])
  await session.stop()
})

test('a DM is sent and its ACK is correlated by the expected code', async () => {
  const { transport, session } = makeSession()
  await session.start()

  const info = await session.sendMessage({
    to: Uint8Array.of(0x01, 0x23, 0x45, 0x67, 0x89, 0xab),
    text: '>d',
    timestamp: 1_704_067_200_000,
    attempt: 0,
  })

  assert.equal(transport.messageSends.length, 1)
  assert.equal(transport.messageSends[0].text, '>d')
  assert.equal(transport.messageSends[0].attempt, 0)
  assert.equal(info.suggestedTimeoutMs, 8000)
  assert.equal(info.expectedAck.length, 4)

  const waiting = session.waitForAcknowledgement({ expectedAck: info.expectedAck, timeout: 1 })
  // A stranger's ACK first: not ours, so it must not settle the wait.
  transport.deliverAcknowledgement({ code: Uint8Array.of(0xff, 0xff, 0xff, 0xff) })
  transport.deliverAcknowledgement({ code: info.expectedAck, tripTime: 1234 })

  const event = await waiting
  assert.ok(event != null, 'the matching ACK resolves the wait')
  assert.equal(event.tripTime, 1234)

  await session.stop()
})

test('getContacts walks the iteration protocol', async () => {
  const contacts = [
    makeFakeContact({ publicKey: new Uint8Array(32).fill(0x01), advertisedName: 'WX-AUS' }),
    makeFakeContact({ publicKey: new Uint8Array(32).fill(0x02), advertisedName: 'Repeater' }),
  ]
  const { session } = makeSession({ contacts })
  await session.start()

  const rows = await session.getContacts()
  assert.equal(rows.length, 2)
  assert.deepEqual(
    rows.map((row) => row.advertisedName),
    ['WX-AUS', 'Repeater'],
  )

  await session.stop()
})

test('resetPath insists on the full 32-byte key', async () => {
  const { session } = makeSession()
  await session.start()

  await assert.rejects(
    () => session.resetPath({ publicKey: new Uint8Array(6) }),
    (error) => error.kind === 'invalidInput',
  )
  await session.resetPath({ publicKey: new Uint8Array(32).fill(0x11) })

  await session.stop()
})

test('losing the transport moves the session to disconnected', async () => {
  const { transport, session } = makeSession()
  await session.start()

  const states = []
  session.subscribe((event) => {
    if (event.kind === 'connectionStateChanged') states.push(event.value)
  })

  transport.onDisconnect()
  assert.deepEqual(states, ['disconnected'])
  assert.equal(session.state, 'disconnected')
})

test('an unmodelled push is surfaced, not thrown', async () => {
  const { transport, session } = makeSession()
  const seen = []
  session.subscribe((event) => seen.push(event))
  await session.start()

  transport.deliverRaw(Uint8Array.of(0xf7, 0x01, 0x02))
  await waitFor(() => seen.some((event) => event.kind === 'unknown'))

  const unknown = seen.find((event) => event.kind === 'unknown')
  assert.equal(unknown.code, 0xf7)
  await session.stop()
})

async function waitFor(predicate, { timeoutMs = 1000 } = {}) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('waitFor timed out')
    await new Promise((resolve) => setTimeout(resolve, 1))
  }
}
