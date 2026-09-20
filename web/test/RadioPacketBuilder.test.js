// Ported from `MeshCore/Tests/MeshCoreTests/Validation/PythonReferenceTests.swift` and
// `Protocol/V115CommandsTests.swift`: same cases, same numbers.

import test from 'node:test'
import assert from 'node:assert/strict'
import { PacketBuilder, channelDataMaxPayloadBytes, hexString } from '../src/radio/index.js'
import {
  PythonReferenceBytes,
  REFERENCE_TIMESTAMP_MS,
  pythonSetChannel,
} from './helpers/radio-fixtures.js'

const hex = (bytes) => hexString(bytes)

test('appStart matches Python', () => {
  assert.equal(hex(PacketBuilder.appStart({ clientId: 'MCore' })), hex(PythonReferenceBytes.appStart))
})

test('appStart truncates the client id to five bytes', () => {
  const packet = PacketBuilder.appStart({ clientId: 'MeshCore-Web' })
  assert.equal(packet.length, 8 + 5)
  assert.equal(new TextDecoder().decode(packet.subarray(8)), 'MeshC')
})

test('deviceQuery matches Python', () => {
  assert.equal(hex(PacketBuilder.deviceQuery()), hex(PythonReferenceBytes.deviceQuery))
})

test('getBattery matches Python', () => {
  assert.equal(hex(PacketBuilder.getBattery()), hex(PythonReferenceBytes.getBattery))
})

test('getTime matches Python', () => {
  assert.equal(hex(PacketBuilder.getTime()), hex(PythonReferenceBytes.getTime))
})

test('setTime matches Python', () => {
  assert.equal(
    hex(PacketBuilder.setTime(REFERENCE_TIMESTAMP_MS)),
    hex(PythonReferenceBytes.setTime_1704067200),
  )
})

test('setName matches Python', () => {
  assert.equal(hex(PacketBuilder.setName('TestNode')), hex(PythonReferenceBytes.setName_TestNode))
})

test('sendAdvertisement matches Python', () => {
  assert.equal(hex(PacketBuilder.sendAdvertisement()), hex(PythonReferenceBytes.sendAdvertisement))
  assert.equal(
    hex(PacketBuilder.sendAdvertisement({ flood: true })),
    hex(PythonReferenceBytes.sendAdvertisement_flood),
  )
})

test('reboot matches Python', () => {
  assert.equal(hex(PacketBuilder.reboot()), hex(PythonReferenceBytes.reboot))
})

test('getContacts matches Python', () => {
  assert.equal(hex(PacketBuilder.getContacts()), hex(PythonReferenceBytes.getContacts))
})

test('getContacts with a since date appends a u32 LE timestamp', () => {
  const packet = PacketBuilder.getContacts({ since: REFERENCE_TIMESTAMP_MS })
  assert.equal(hex(packet), '0480009265')
})

test('getMessage matches Python', () => {
  assert.equal(hex(PacketBuilder.getMessage()), hex(PythonReferenceBytes.getMessage))
})

test('sendMessage matches Python', () => {
  const packet = PacketBuilder.sendMessage({
    to: Uint8Array.of(0x01, 0x23, 0x45, 0x67, 0x89, 0xab),
    text: 'Hello',
    timestamp: REFERENCE_TIMESTAMP_MS,
  })
  assert.equal(hex(packet), hex(PythonReferenceBytes.sendMessage_Hello))
})

test('sendMessage carries the attempt counter at offset 2', () => {
  const packet = PacketBuilder.sendMessage({
    to: Uint8Array.of(0x01, 0x23, 0x45, 0x67, 0x89, 0xab),
    text: 'Hello',
    timestamp: REFERENCE_TIMESTAMP_MS,
    attempt: 2,
  })
  assert.equal(packet[2], 2)
})

test('sendChannelMessage matches Python', () => {
  const packet = PacketBuilder.sendChannelMessage({
    channel: 0,
    text: 'Hi',
    timestamp: REFERENCE_TIMESTAMP_MS,
  })
  assert.equal(hex(packet), hex(PythonReferenceBytes.sendChannelMessage_0_Hi))
})

test('getChannel matches Python', () => {
  assert.equal(hex(PacketBuilder.getChannel({ index: 0 })), hex(PythonReferenceBytes.getChannel_0))
})

test('setChannel matches Python', () => {
  const secret = new Uint8Array(16).fill(0xab)
  const packet = PacketBuilder.setChannel({ index: 0, name: 'General', secret })
  assert.equal(hex(packet), hex(pythonSetChannel(0, 'General', secret)))
  assert.equal(packet.length, 2 + 32 + 16)
})

test('resetPath carries the full 32-byte key', () => {
  const publicKey = new Uint8Array(32).fill(0x11)
  const packet = PacketBuilder.resetPath({ publicKey })
  assert.equal(packet[0], 0x0d)
  assert.equal(packet.length, 33)
})

// MARK: - sendChannelData (v1.15.0)

test('sendChannelData flood (default pathLength) format', () => {
  const payload = Uint8Array.of(0xaa, 0xbb, 0xcc)
  const packet = PacketBuilder.sendChannelData({ channelIndex: 2, dataType: 0xffff, payload })

  assert.equal(packet[0], 0x3e, 'Command code')
  assert.equal(packet[1], 0x02, 'Channel index')
  assert.equal(packet[2], 0xff, 'pathLength defaults to 0xFF (flood)')
  assert.equal(packet[3], 0xff, 'data_type LE low byte')
  assert.equal(packet[4], 0xff, 'data_type LE high byte')
  assert.deepEqual(packet.slice(5), payload, 'Payload follows data_type')
  assert.equal(packet.length, 5 + payload.length)
})

test('sendChannelData flood ignores pathBytes when pathLength == 0xFF', () => {
  const packet = PacketBuilder.sendChannelData({
    channelIndex: 0,
    dataType: 0xffff,
    payload: Uint8Array.of(0xaa),
    pathLength: 0xff,
    pathBytes: Uint8Array.of(0xde, 0xad, 0xbe, 0xef),
  })
  assert.equal(packet.length, 5 + 1, '5-byte header (flood) + 1-byte payload; pathBytes dropped')
  assert.equal(packet[2], 0xff)
})

test('sendChannelData direct-path format (1-byte hashes)', () => {
  // pathLength 0x03: upper 2 bits = 0b00 -> hash_size = 1; lower 6 bits = 3 -> hash_count = 3.
  const pathBytes = Uint8Array.of(0x11, 0x22, 0x33)
  const payload = Uint8Array.of(0x01, 0x02)
  const packet = PacketBuilder.sendChannelData({
    channelIndex: 0,
    dataType: 0x1234,
    payload,
    pathLength: 0x03,
    pathBytes,
  })

  assert.equal(packet[0], 0x3e)
  assert.equal(packet[1], 0x00, 'Channel 0')
  assert.equal(packet[2], 0x03, 'Encoded pathLength byte is passed through verbatim')
  assert.deepEqual(packet.slice(3, 6), pathBytes, 'Path bytes follow pathLength')
  assert.equal(packet[6], 0x34, 'data_type LE low')
  assert.equal(packet[7], 0x12, 'data_type LE high')
  assert.deepEqual(packet.slice(8), payload, 'Payload tail')
  assert.equal(packet.length, 8 + payload.length)
})

test('sendChannelData direct-path format (2-byte hashes)', () => {
  // pathLength 0x42: hash_size = 2, hash_count = 2, so firmware consumes 4 path bytes.
  const pathBytes = Uint8Array.of(0x11, 0x22, 0x33, 0x44)
  const packet = PacketBuilder.sendChannelData({
    channelIndex: 1,
    dataType: 0xffff,
    payload: Uint8Array.of(0xaa),
    pathLength: 0x42,
    pathBytes,
  })
  assert.equal(packet[2], 0x42)
  assert.deepEqual(packet.slice(3, 7), pathBytes)
  assert.equal(packet[7], 0xff)
  assert.equal(packet[8], 0xff)
  assert.deepEqual(packet.slice(9), Uint8Array.of(0xaa))
})

test('sendChannelData passes caller-supplied pathBytes verbatim', () => {
  // The builder is a dumb packer. If the caller lies about pathLength, firmware rejects it;
  // we do not second-guess on our side.
  const packet = PacketBuilder.sendChannelData({
    channelIndex: 0,
    dataType: 0xffff,
    payload: Uint8Array.of(0x00),
    pathLength: 0x05,
    pathBytes: Uint8Array.of(0x01, 0x02), // caller says 5, passes only 2
  })
  assert.equal(packet[2], 0x05)
  assert.deepEqual(packet.slice(3, 5), Uint8Array.of(0x01, 0x02))
})

test('sendChannelData clamps the payload to 163 bytes', () => {
  const payload = new Uint8Array(200).fill(0x55)
  const packet = PacketBuilder.sendChannelData({ channelIndex: 1, dataType: 0x00ff, payload })

  assert.equal(packet.length, 5 + channelDataMaxPayloadBytes)
  assert.deepEqual(packet.slice(5), new Uint8Array(channelDataMaxPayloadBytes).fill(0x55))
})

test('getStats commands name their category', () => {
  assert.equal(hex(PacketBuilder.getStatsCore()), '3800')
  assert.equal(hex(PacketBuilder.getStatsRadio()), '3801')
  assert.equal(hex(PacketBuilder.getStatsPackets()), '3802')
})

test('epochSeconds32 saturates instead of overflowing', () => {
  assert.equal(PacketBuilder.epochSeconds32(-1000), 0)
  assert.equal(PacketBuilder.epochSeconds32(0), 0)
  assert.equal(PacketBuilder.epochSeconds32(1e16), 0xffffffff)
})
