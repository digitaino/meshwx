// `src/link/SessionWeatherTransport.js` over a `FakeRadioTransport`: the slot scan, adding
// the channel, the Request datagram's bytes against `docs/meshwx_v5_vectors.json`, a 0x1B
// datagram end to end, and the three ways a channel request is refused.

import test from 'node:test'
import assert from 'node:assert/strict'
import { FakeRadioTransport, MeshCoreSession, hexString, utf8Encode } from '../src/radio/index.js'
import {
  CHANNEL_SLOTS,
  MESHWX_DATA_TYPE,
  SessionWeatherTransport,
  WeatherChannel,
  addWeatherChannel,
  encodeRequestDatagram,
  findWeatherSlot,
} from '../src/link/SessionWeatherTransport.js'

const RADIO_KEY = Uint8Array.from(Array.from({ length: 32 }, (_, i) => (i + 1) & 0xff))

async function connectedSession({ channels = new Map(), ...options } = {}) {
  const transport = new FakeRadioTransport({ publicKey: RADIO_KEY, channels, ...options })
  const session = new MeshCoreSession(transport)
  await session.start()
  return { transport, session }
}

async function weatherChannels(slot) {
  const channels = new Map()
  channels.set(slot, { name: WeatherChannel.name, secret: await WeatherChannel.secret() })
  return channels
}

// MARK: - The wire vector

test('the Request datagram matches the request_digest vector', () => {
  // docs/meshwx_v5_vectors.json, `request_digest`: `>d` to bot 0x041D from sender 01..06 at
  // ts 1789660000, seq 1 — 16 bytes.
  const bytes = encodeRequestDatagram({
    seq: 1,
    botID: 0x041d,
    senderPrefix: Uint8Array.of(0x01, 0x02, 0x03, 0x04, 0x05, 0x06),
    timestamp: 1_789_660_000,
    text: '>d',
  })

  assert.equal(hexString(bytes), '011d0490010203040506600bac6a3e64')
  assert.equal(bytes.length, 16)
})

test('the Request datagram refuses what the spec refuses', () => {
  const ok = {
    seq: 1,
    botID: 0xffff,
    senderPrefix: new Uint8Array(6),
    timestamp: 1_789_660_000,
    text: '>d',
  }
  assert.throws(() => encodeRequestDatagram({ ...ok, senderPrefix: new Uint8Array(5) }))
  // The refusals are the codec's (`MeshWXEncodeError`): no `>`, nothing after it, over 40 bytes.
  assert.throws(() => encodeRequestDatagram({ ...ok, text: 'd' }))
  assert.throws(() => encodeRequestDatagram({ ...ok, text: '>' }))
  assert.throws(() => encodeRequestDatagram({ ...ok, text: `>${'x'.repeat(40)}` }))
  assert.doesNotThrow(() => encodeRequestDatagram({ ...ok, text: `>${'x'.repeat(39)}` }))
})

test('the #meshwx secret is the first 16 bytes of SHA-256 of the name', async () => {
  const secret = await WeatherChannel.secret()
  assert.equal(secret.length, 16)
  // SHA256("#meshwx") = 9b3f9b2e… ; the channel key is its first 16 bytes.
  const digest = new Uint8Array(
    await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode('#meshwx')),
  )
  assert.equal(hexString(secret), hexString(digest.slice(0, 16)))
})

// MARK: - Slots

test('the slot scan finds #meshwx at slot 31 of 40', async () => {
  const { session } = await connectedSession({ channels: await weatherChannels(31) })

  const slot = await findWeatherSlot(session)
  assert.equal(slot, 31)
  assert.ok(slot < CHANNEL_SLOTS)

  await session.stop()
})

test('a radio with no #meshwx slot answers null, and a short radio does not hang', async () => {
  const { session } = await connectedSession({ maxChannels: 8 })
  assert.equal(await findWeatherSlot(session), null)
  await session.stop()
})

test('adding the channel writes the first free slot above 0 and re-reads it first', async () => {
  const channels = new Map()
  channels.set(0, { name: 'Public', secret: new Uint8Array(16).fill(0x01) })
  channels.set(1, { name: 'Neighbours', secret: new Uint8Array(16).fill(0x02) })
  const { transport, session } = await connectedSession({ channels, maxChannels: 8 })

  const result = await addWeatherChannel(session)
  assert.deepEqual(result, { index: 2, added: true })
  assert.equal(transport.channels.get(2).name, '#meshwx')
  assert.equal(hexString(transport.channels.get(2).secret), hexString(await WeatherChannel.secret()))
  // The slot was read back before it was written.
  const frames = transport.sentFrames.map((frame) => [frame[0], frame[1]])
  const readIndex = frames.findIndex(([code, index]) => code === 0x1f && index === 2)
  const writeIndex = frames.findIndex(([code, index]) => code === 0x20 && index === 2)
  assert.ok(readIndex >= 0 && readIndex < writeIndex, 'getChannel(2) precedes setChannel(2)')

  await session.stop()
})

test('adding the channel is a no-op when a slot already carries it', async () => {
  const { transport, session } = await connectedSession({ channels: await weatherChannels(31) })

  const result = await addWeatherChannel(session)
  assert.deepEqual(result, { index: 31, added: false })
  assert.ok(!transport.sentFrames.some((frame) => frame[0] === 0x20), 'nothing was written')

  await session.stop()
})

test('a full radio refuses to add the channel rather than overwriting someone else', async () => {
  const channels = new Map()
  for (let index = 0; index < 4; index += 1) {
    channels.set(index, { name: `ch${index}`, secret: new Uint8Array(16).fill(index + 1) })
  }
  const { session } = await connectedSession({ channels, maxChannels: 4 })

  await assert.rejects(
    () => addWeatherChannel(session),
    (error) => error.kind === 'channelRequestsUnavailable',
  )

  await session.stop()
})

// MARK: - Sending

test('a channel request goes out as a flooded datagram whose bytes are the vector', async () => {
  const { transport, session } = await connectedSession({ channels: await weatherChannels(31) })
  const weather = new SessionWeatherTransport({ session })

  const forwarded = []
  transport.onChannelData((record) => forwarded.push(record))

  await weather.sendChannelRequest({
    text: '>d',
    botID: 0x041d,
    timestamp: 1_789_660_000_000,
    seq: 1,
  })

  assert.equal(transport.channelDataSends.length, 1)
  const sent = transport.channelDataSends[0]
  assert.equal(sent.channelIndex, 31)
  assert.equal(sent.dataType, MESHWX_DATA_TYPE)
  assert.equal(sent.pathLength, 0xff, 'flooded: no stored route can lose it')
  // The radio's own key is 01 02 03 04 05 06 …, which is the vector's sender prefix.
  assert.equal(hexString(sent.payload), '011d0490010203040506600bac6a3e64')
  assert.equal(forwarded.length, 1, 'a host can forward the datagram elsewhere')

  await session.stop()
})

test('a channel request is refused when the firmware is too old', async () => {
  const { session } = await connectedSession({
    firmwareVersion: 10,
    channels: await weatherChannels(31),
  })
  const weather = new SessionWeatherTransport({ session })

  await assert.rejects(
    () => weather.sendChannelRequest({ text: '>d', botID: 0x041d, timestamp: 0, seq: 1 }),
    (error) => error.kind === 'channelRequestsUnavailable' && /older than v1\.15\.0/.test(error.reason),
  )

  await session.stop()
})

test('a channel request is refused when no slot carries #meshwx', async () => {
  const { session } = await connectedSession({ maxChannels: 4 })
  const weather = new SessionWeatherTransport({ session })

  await assert.rejects(
    () => weather.sendChannelRequest({ text: '>d', botID: 0x041d, timestamp: 0, seq: 1 }),
    (error) => error.kind === 'channelRequestsUnavailable' && /no slot/.test(error.reason),
  )

  await session.stop()
})

test('a channel request is refused when the radio has no public key', async () => {
  const { session } = await connectedSession({ channels: await weatherChannels(31) })
  const weather = new SessionWeatherTransport({
    session: { ...proxySession(session), selfInfo: null },
  })

  await assert.rejects(
    () => weather.sendChannelRequest({ text: '>d', botID: 0x041d, timestamp: 0, seq: 1 }),
    (error) => error.kind === 'channelRequestsUnavailable' && /public key/.test(error.reason),
  )

  await session.stop()
})

test('the stored slot is used before any slot is scanned', async () => {
  const { transport, session } = await connectedSession({ channels: await weatherChannels(31) })
  const weather = new SessionWeatherTransport({ session, storedWeatherSlot: async () => 31 })
  const before = transport.sentFrames.length

  await weather.sendChannelRequest({ text: '>d', botID: 0x041d, timestamp: 0, seq: 1 })

  const reads = transport.sentFrames.slice(before).filter((frame) => frame[0] === 0x1f)
  assert.equal(reads.length, 0, 'no getChannel round trips when the table already knows')
  assert.equal(transport.channelDataSends[0].channelIndex, 31)

  await session.stop()
})

test('sendRequest returns the ACK code the radio expects, and ACKs are subscribed', async () => {
  const { transport, session } = await connectedSession()
  const weather = new SessionWeatherTransport({ session })

  const codes = []
  const unsubscribe = weather.subscribeAcknowledgements((code) => codes.push(hexString(code)))

  const expectedAck = await weather.sendRequest({
    to: new Uint8Array(32).fill(0x0a),
    text: '>d',
    timestamp: 1_789_660_000_000,
    attempt: 1,
  })
  assert.equal(expectedAck.length, 4)
  assert.equal(transport.messageSends[0].attempt, 1)

  transport.deliverAcknowledgement({ code: expectedAck })
  await tick()
  assert.deepEqual(codes, [hexString(expectedAck)])

  unsubscribe()
  transport.deliverAcknowledgement({ code: expectedAck })
  await tick()
  assert.equal(codes.length, 1, 'unsubscribe stops the stream')

  await session.stop()
})

test('a 0x1B datagram arrives end to end, with backlog told from live', async () => {
  const transport = new FakeRadioTransport({
    publicKey: RADIO_KEY,
    channels: await weatherChannels(31),
  })
  // One datagram queued before the browser connected: backlog.
  transport.deliverChannelData({
    channelIndex: 31,
    dataType: MESHWX_DATA_TYPE,
    data: utf8Encode('queued'),
    snr: 5,
  })

  const session = new MeshCoreSession(transport)
  const weather = new SessionWeatherTransport({ session })
  const received = []
  weather.subscribeDatagrams((datagram) => {
    received.push({ datagram, backlog: session.isDrainingBacklog })
  })

  await session.start()

  assert.equal(received.length, 1)
  assert.equal(received[0].datagram.channelIndex, 31)
  assert.equal(received[0].datagram.dataType, MESHWX_DATA_TYPE)
  assert.equal(received[0].datagram.snr, 5)
  assert.equal(new TextDecoder().decode(received[0].datagram.data), 'queued')
  assert.equal(received[0].backlog, true, 'delivered during the connect drain')
  assert.equal(await weather.isDrainingBacklog(), false, 'the drain is over')

  // A second one after connect: live.
  transport.deliverChannelData({
    channelIndex: 31,
    dataType: MESHWX_DATA_TYPE,
    data: utf8Encode('live'),
  })
  await waitFor(() => received.length === 2)
  assert.equal(received[1].backlog, false)
  assert.equal(new TextDecoder().decode(received[1].datagram.data), 'live')

  await session.stop()
})

test('channelSecret reads the radio, and a stored weather secret short-circuits it', async () => {
  const { transport, session } = await connectedSession({ channels: await weatherChannels(31) })
  const secret = await WeatherChannel.secret()

  const plain = new SessionWeatherTransport({ session })
  assert.equal(hexString(await plain.channelSecret({ at: 31 })), hexString(secret))

  const cached = new SessionWeatherTransport({
    session,
    storedChannelSecret: async (index) => (index === 31 ? secret : null),
  })
  const before = transport.sentFrames.length
  assert.equal(hexString(await cached.channelSecret({ at: 31 })), hexString(secret))
  assert.equal(transport.sentFrames.length, before, 'no round trip when the table already knows')

  await session.stop()
})

test('linkState is null over a radio', async () => {
  const { session } = await connectedSession()
  const weather = new SessionWeatherTransport({ session })
  assert.equal(await weather.linkState(), null)
  await session.stop()
})

test('resetPath reaches the radio', async () => {
  const { transport, session } = await connectedSession()
  const weather = new SessionWeatherTransport({ session })

  await weather.resetPath({ to: new Uint8Array(32).fill(0x0b) })
  assert.ok(transport.sentFrames.some((frame) => frame[0] === 0x0d && frame.length === 33))

  await session.stop()
})

/** A shallow stand-in that keeps the session's methods bound but lets a field be overridden. */
function proxySession(session) {
  return {
    subscribe: (fn) => session.subscribe(fn),
    sendMessage: (args) => session.sendMessage(args),
    sendChannelData: (args) => session.sendChannelData(args),
    getChannel: (args) => session.getChannel(args),
    setChannel: (args) => session.setChannel(args),
    resetPath: (args) => session.resetPath(args),
    get isDrainingBacklog() {
      return session.isDrainingBacklog
    },
    get supportsChannelDatagrams() {
      return session.supportsChannelDatagrams
    },
    get deviceInfo() {
      return session.deviceInfo
    },
    selfInfo: session.selfInfo,
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))

async function waitFor(predicate, { timeoutMs = 1000 } = {}) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('waitFor timed out')
    await new Promise((resolve) => setTimeout(resolve, 1))
  }
}
