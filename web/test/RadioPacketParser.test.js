// Ported from `MeshCore/Tests/MeshCoreTests/Validation/V115ParsingTests.swift` and the
// response cases of `Validation/NewResponseParsingTests.swift` / `Protocol/AckParsingTests.swift`,
// plus the cases this client's own responses need.

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  PacketParser,
  Parsers,
  concatBytes,
  u32LE,
  utf8Encode,
  utf8PaddedOrTruncated,
} from '../src/radio/index.js'
import { selfInfoFrame } from './helpers/radio-fixtures.js'

// MARK: - ChannelDatagram (0x1B)

test('channelDatagram parses a valid payload', () => {
  // Frame after PacketParser strips the 0x1B response byte:
  // [snr:1][rsv:1][rsv:1][channel:1][path_len:1][data_type LE:2][data_len:1][data...]
  const data = Uint8Array.of(
    20, 0x00, 0x00, // snr 20/4 = 5.0 dB + reserved
    0x03, // channel index
    0xff, // path_len: 0xFF means direct route
    0xff, 0xff, // data_type LE 0xFFFF (DEV)
    0x04, // data_len
    0xde, 0xad, 0xbe, 0xef,
  )

  const event = Parsers.ChannelDatagram.parse(data)
  assert.equal(event.kind, 'channelDataReceived')
  assert.equal(event.value.channelIndex, 3)
  assert.equal(event.value.pathLength, 0xff)
  assert.equal(event.value.dataType, 0xffff)
  assert.deepEqual(event.value.data, Uint8Array.of(0xde, 0xad, 0xbe, 0xef))
  assert.equal(event.value.snr, 5.0)
})

test('channelDatagram routes via PacketParser', () => {
  const data = Uint8Array.of(
    0x1b, // response code
    0x00, 0x00, 0x00,
    0x00, // channel 0
    0x03, // path_len: flood-accumulated, 3 hops × 1-byte hashes
    0x12, 0x34, // data_type LE 0x3412
    0x02,
    0xaa, 0xbb,
  )

  const event = PacketParser.parse(data)
  assert.equal(event.kind, 'channelDataReceived')
  assert.equal(event.value.pathLength, 0x03)
  assert.equal(event.value.dataType, 0x3412)
  assert.deepEqual(event.value.data, Uint8Array.of(0xaa, 0xbb))
})

test('channelDatagram rejects a truncated payload', () => {
  const event = Parsers.ChannelDatagram.parse(Uint8Array.of(0x00, 0x00, 0x00, 0x01, 0xff))
  assert.equal(event.kind, 'parseFailure')
})

test('channelDatagram truncates when declared data_len exceeds the remaining bytes', () => {
  // Firmware emits data_len == the actual payload; a buggy peer that lies is clamped to what
  // is really there rather than failing the whole frame.
  const event = Parsers.ChannelDatagram.parse(
    Uint8Array.of(0x00, 0x00, 0x00, 0x01, 0xff, 0xff, 0xff, 0x10, 0xaa, 0xbb),
  )
  assert.equal(event.kind, 'channelDataReceived')
  assert.deepEqual(event.value.data, Uint8Array.of(0xaa, 0xbb))
})

test('channelDatagram reads a negative SNR', () => {
  const event = Parsers.ChannelDatagram.parse(
    Uint8Array.of(0xec, 0x00, 0x00, 0x01, 0xff, 0x10, 0xff, 0x01, 0x42),
  )
  assert.equal(event.value.snr, -5)
  assert.equal(event.value.dataType, 0xff10)
})

// MARK: - Messages

test('channel message v3 parses SNR, channel, timestamp and text', () => {
  const frame = concatBytes(
    0x11, // channelMessageReceivedV3
    20, 0x00, 0x00,
    0x02, // channel
    0xff, // path len
    0x00, // text type
    u32LE(1_704_067_200),
    utf8Encode('hi there'),
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'channelMessageReceived')
  assert.equal(event.value.channelIndex, 2)
  assert.equal(event.value.snr, 5)
  assert.equal(event.value.senderTimestamp, 1_704_067_200 * 1000)
  assert.equal(event.value.text, 'hi there')
})

test('channel message v1 carries no SNR', () => {
  const frame = concatBytes(
    0x08, // channelMessageReceived
    0x02,
    0xff,
    0x00,
    u32LE(1_704_067_200),
    utf8Encode('hi'),
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'channelMessageReceived')
  assert.equal(event.value.snr, null)
  assert.equal(event.value.text, 'hi')
})

test('contact message v3 parses the sender prefix and text', () => {
  const prefix = Uint8Array.of(0x01, 0x02, 0x03, 0x04, 0x05, 0x06)
  const frame = concatBytes(
    0x10, // contactMessageReceivedV3
    16, 0x00, 0x00,
    prefix,
    0xff,
    0x00,
    u32LE(1_704_067_200),
    utf8Encode('ping'),
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'contactMessageReceived')
  assert.deepEqual(event.value.senderPublicKeyPrefix, prefix)
  assert.equal(event.value.snr, 4)
  assert.equal(event.value.signature, null)
  assert.equal(event.value.text, 'ping')
})

test('a signed contact message carries a four-byte signature before the text', () => {
  const prefix = new Uint8Array(6).fill(0x07)
  const frame = concatBytes(
    0x10,
    0, 0x00, 0x00,
    prefix,
    0xff,
    0x02, // text type: signed
    u32LE(1_704_067_200),
    Uint8Array.of(0xaa, 0xbb, 0xcc, 0xdd),
    utf8Encode('signed'),
  )
  const event = PacketParser.parse(frame)
  assert.deepEqual(event.value.signature, Uint8Array.of(0xaa, 0xbb, 0xcc, 0xdd))
  assert.equal(event.value.text, 'signed')
})

test('noMoreMessages and messagesWaiting are bare pushes', () => {
  assert.equal(PacketParser.parse(Uint8Array.of(0x0a)).kind, 'noMoreMessages')
  assert.equal(PacketParser.parse(Uint8Array.of(0x83)).kind, 'messagesWaiting')
})

test('messageSent carries the route, expected ACK and suggested timeout', () => {
  const frame = concatBytes(
    0x06,
    0x01, // route: flood
    Uint8Array.of(0xde, 0xad, 0xbe, 0xef),
    u32LE(8000),
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'messageSent')
  assert.equal(event.value.route, 1)
  assert.deepEqual(event.value.expectedAck, Uint8Array.of(0xde, 0xad, 0xbe, 0xef))
  assert.equal(event.value.suggestedTimeoutMs, 8000)
})

test('messageSent shorter than nine bytes is a parse failure', () => {
  assert.equal(PacketParser.parse(Uint8Array.of(0x06, 0x01, 0x02)).kind, 'parseFailure')
})

// MARK: - Acknowledgements

test('an ACK without trip time parses the four-byte code', () => {
  const event = PacketParser.parse(Uint8Array.of(0x82, 0x01, 0x02, 0x03, 0x04))
  assert.equal(event.kind, 'acknowledgement')
  assert.deepEqual(event.code, Uint8Array.of(0x01, 0x02, 0x03, 0x04))
  assert.equal(event.tripTime, null)
})

test('an ACK with trip time parses both', () => {
  const event = PacketParser.parse(concatBytes(0x82, Uint8Array.of(1, 2, 3, 4), u32LE(1234)))
  assert.equal(event.tripTime, 1234)
})

test('an ACK shorter than four bytes is a parse failure', () => {
  assert.equal(PacketParser.parse(Uint8Array.of(0x82, 0x01)).kind, 'parseFailure')
})

// MARK: - Device

test('selfInfo parses the public key and name', () => {
  const publicKey = new Uint8Array(32).fill(0x42)
  const event = PacketParser.parse(selfInfoFrame({ publicKey, name: 'WX-AUS' }))
  assert.equal(event.kind, 'selfInfo')
  assert.deepEqual(event.value.publicKey, publicKey)
  assert.equal(event.value.name, 'WX-AUS')
  assert.equal(event.value.radioFrequency, 906.875)
  assert.equal(event.value.radioSpreadingFactor, 11)
})

test('selfInfo shorter than 57 bytes is a parse failure', () => {
  assert.equal(PacketParser.parse(concatBytes(0x05, new Uint8Array(20))).kind, 'parseFailure')
})

test('deviceInfo v3+ parses the version code, maxima and strings', () => {
  const frame = concatBytes(
    0x0d,
    11, // firmware version code
    50, // max contacts / 2
    40, // max channels
    u32LE(123456),
    utf8PaddedOrTruncated('20260101', 12),
    utf8PaddedOrTruncated('Heltec V3', 40),
    utf8PaddedOrTruncated('1.15.0', 20),
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'deviceInfo')
  assert.equal(event.value.firmwareVersion, 11)
  assert.equal(event.value.maxContacts, 100, 'stored as count/2 in firmware')
  assert.equal(event.value.maxChannels, 40)
  assert.equal(event.value.model, 'Heltec V3')
  assert.equal(event.value.version, '1.15.0')
})

test('deviceInfo v3+ shorter than 79 bytes is a parse failure', () => {
  assert.equal(PacketParser.parse(Uint8Array.of(0x0d, 11, 50, 40)).kind, 'parseFailure')
})

test('battery parses level alone and level with storage', () => {
  const bare = PacketParser.parse(Uint8Array.of(0x0c, 0x04, 0x10))
  assert.equal(bare.kind, 'battery')
  assert.equal(bare.value.level, 0x1004)
  assert.equal(bare.value.usedStorageKB, null)

  const extended = PacketParser.parse(concatBytes(0x0c, Uint8Array.of(0x04, 0x10), u32LE(120), u32LE(1024)))
  assert.equal(extended.value.usedStorageKB, 120)
  assert.equal(extended.value.totalStorageKB, 1024)
})

test('currentTime is milliseconds', () => {
  const event = PacketParser.parse(concatBytes(0x09, u32LE(1_704_067_200)))
  assert.equal(event.kind, 'currentTime')
  assert.equal(event.value, 1_704_067_200 * 1000)
})

test('coreStats parses the battery, uptime, errors and queue length', () => {
  const frame = concatBytes(
    0x18,
    0x00, // stats type: core
    Uint8Array.of(0x04, 0x10),
    u32LE(3600),
    Uint8Array.of(0x02, 0x00),
    0x03,
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'statsCore')
  assert.equal(event.value.batteryMV, 0x1004)
  assert.equal(event.value.uptimeSeconds, 3600)
  assert.equal(event.value.errors, 2)
  assert.equal(event.value.queueLength, 3)
})

// MARK: - Channels and contacts

test('channelInfo stops the name at the null terminator', () => {
  const nameField = new Uint8Array(32).fill(0x5a) // garbage after the null
  nameField.set(utf8Encode('#meshwx'))
  nameField[7] = 0
  const secret = new Uint8Array(16).fill(0x7e)
  const event = PacketParser.parse(concatBytes(0x12, 0x1f, nameField, secret))

  assert.equal(event.kind, 'channelInfo')
  assert.equal(event.value.index, 31)
  assert.equal(event.value.name, '#meshwx')
  assert.deepEqual(event.value.secret, secret)
})

test('channelInfo shorter than 49 bytes is a parse failure', () => {
  assert.equal(PacketParser.parse(concatBytes(0x12, new Uint8Array(20))).kind, 'parseFailure')
})

test('a contact row parses its key, name and flood path', () => {
  const publicKey = new Uint8Array(32).fill(0x09)
  const frame = concatBytes(
    0x03,
    publicKey,
    0x01, // chat
    0x01, // favorite
    0xff, // flood
    new Uint8Array(64),
    utf8PaddedOrTruncated('WX-AUS', 32),
    u32LE(1_704_067_200),
    u32LE(0),
    u32LE(0),
    u32LE(1_704_067_300),
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'contact')
  assert.equal(event.value.advertisedName, 'WX-AUS')
  assert.equal(event.value.outPathLength, 0xff)
  assert.equal(event.value.type, 0x01)
  assert.equal(event.value.lastAdvertisement, 1_704_067_200 * 1000)
  assert.equal(event.value.id.slice(0, 4), '0909')
})

test('a contact row with the reserved path encoding is refused', () => {
  const frame = concatBytes(
    0x03,
    new Uint8Array(32),
    0x01,
    0x00,
    0xc1, // mode 3 is reserved
    new Uint8Array(64),
    new Uint8Array(32),
    u32LE(0),
    u32LE(0),
    u32LE(0),
    u32LE(0),
  )
  assert.equal(PacketParser.parse(frame).kind, 'parseFailure')
})

test('contactsStart and contactsEnd bracket the iteration', () => {
  const start = PacketParser.parse(concatBytes(0x02, u32LE(3)))
  assert.equal(start.kind, 'contactsStart')
  assert.equal(start.count, 3)

  const end = PacketParser.parse(concatBytes(0x04, u32LE(1_704_067_200)))
  assert.equal(end.kind, 'contactsEnd')
  assert.equal(end.lastModified, 1_704_067_200 * 1000)
})

test('a new advertisement carries a whole contact record', () => {
  const publicKey = new Uint8Array(32).fill(0x33)
  const frame = concatBytes(
    0x8a,
    publicKey,
    0x01,
    0x00,
    0xff,
    new Uint8Array(64),
    utf8PaddedOrTruncated('New Node', 32),
    u32LE(0),
    u32LE(0),
    u32LE(0),
    u32LE(0),
  )
  const event = PacketParser.parse(frame)
  assert.equal(event.kind, 'newContact')
  assert.equal(event.value.advertisedName, 'New Node')
})

test('an advertisement push carries a 32-byte key', () => {
  const publicKey = new Uint8Array(32).fill(0x77)
  const event = PacketParser.parse(concatBytes(0x80, publicKey))
  assert.equal(event.kind, 'advertisement')
  assert.deepEqual(event.publicKey, publicKey)
})

// MARK: - Simple responses and the unknown path

test('ok with and without a value', () => {
  assert.deepEqual(PacketParser.parse(Uint8Array.of(0x00)), { kind: 'ok', value: null })
  assert.equal(PacketParser.parse(concatBytes(0x00, u32LE(7))).value, 7)
})

test('error carries the firmware sub-code', () => {
  const event = PacketParser.parse(Uint8Array.of(0x01, 0x02))
  assert.equal(event.kind, 'error')
  assert.equal(event.code, 2)
})

test('an unknown response code becomes an unknown event, not a throw', () => {
  const event = PacketParser.parse(Uint8Array.of(0xf7, 0x01, 0x02))
  assert.equal(event.kind, 'unknown')
  assert.equal(event.code, 0xf7)
  assert.equal(event.name, null)
  assert.deepEqual(event.data, Uint8Array.of(0x01, 0x02))
})

test('a response code we know but do not parse is named on the unknown event', () => {
  const event = PacketParser.parse(Uint8Array.of(0x0b, 0xaa))
  assert.equal(event.kind, 'contactURI')

  const privateKey = PacketParser.parse(Uint8Array.of(0x0e, 0xaa))
  assert.equal(privateKey.kind, 'unknown')
  assert.equal(privateKey.name, 'privateKey')
})

test('an empty packet is a parse failure', () => {
  assert.equal(PacketParser.parse(new Uint8Array(0)).kind, 'parseFailure')
})
