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

// ---------------------------------------------------------------------------
// The configuration commands, against the byte layouts in the Swift builder's doc comments
// (`MeshCore/Protocol/PacketBuilder.swift`: setRadio, setTxPower, setCoordinates,
// setOtherParams). Web only: the iOS app sends these from its own settings screens, and the web
// client's radio settings screen is the reason they are here.

test('setRadio writes frequency in kHz and bandwidth in Hz, both little-endian', () => {
  // The owner's mesh: 910.525 MHz, 62.5 kHz, SF 7, CR 5. 910 525 kHz = 0x000DE4BD,
  // 62 500 Hz = 0x0000F424.
  assert.equal(
    hex(PacketBuilder.setRadio({ frequency: 910.525, bandwidth: 62.5, spreadingFactor: 7, codingRate: 5 })),
    '0bbde40d0024f400000705',
  )
})

test('setRadio appends the client-repeat byte only when it is given', () => {
  const params = { frequency: 910.525, bandwidth: 62.5, spreadingFactor: 7, codingRate: 5 }
  assert.equal(PacketBuilder.setRadio(params).length, 11)
  assert.equal(hex(PacketBuilder.setRadio({ ...params, clientRepeat: true })), '0bbde40d0024f40000070501')
  assert.equal(hex(PacketBuilder.setRadio({ ...params, clientRepeat: false })), '0bbde40d0024f40000070500')
})

test('setRadio rounds rather than truncates the scaled value', () => {
  // 41.7 kHz is 41 700 Hz. Truncating (41.699999999999996 × 1000) would write 41 699.
  assert.equal(PacketBuilder.scaledRadioValue(41.7, PacketBuilder.bandwidthRangeHz), 41_700)
  assert.equal(PacketBuilder.scaledRadioValue(7.8, PacketBuilder.bandwidthRangeHz), 7800)
  assert.equal(PacketBuilder.scaledRadioValue(910.525, PacketBuilder.frequencyRangeKHz), 910_525)
})

test('setRadio saturates a frequency or bandwidth outside the firmware ranges', () => {
  const low = PacketBuilder.setRadio({ frequency: 1, bandwidth: 1, spreadingFactor: 5, codingRate: 5 })
  assert.equal(hex(low), '0bf0490200581b00000505')          // 150 000 kHz, 7 000 Hz
  const high = PacketBuilder.setRadio({ frequency: 9999, bandwidth: 9999, spreadingFactor: 12, codingRate: 8 })
  assert.equal(hex(high), '0ba025260020a107000c08')          // 2 500 000 kHz, 500 000 Hz
  assert.equal(PacketBuilder.scaledRadioValue(NaN, PacketBuilder.frequencyRangeKHz), 150_000)
})

test('setRadio writes the spreading factor and coding rate as given, unclamped', () => {
  // The Swift takes a `UInt8` and does not clamp: the firmware refuses an out-of-range value, and
  // clamping here would silently tune the radio to something nobody asked for.
  const packet = PacketBuilder.setRadio({ frequency: 910.525, bandwidth: 62.5, spreadingFactor: 3, codingRate: 9 })
  assert.equal(packet[9], 3)
  assert.equal(packet[10], 9)
})

test('setTxPower writes one signed byte', () => {
  assert.equal(hex(PacketBuilder.setTxPower(22)), '0c16')
  assert.equal(hex(PacketBuilder.setTxPower(0)), '0c00')
  assert.equal(hex(PacketBuilder.setTxPower(-9)), '0cf7')
  assert.equal(hex(PacketBuilder.setTxPower(30)), '0c1e')
})

test('setCoordinates writes two scaled Int32s and four altitude zeros', () => {
  // Austin: 30.2672, -97.7431 → 30 267 200 and -97 743 100, degrees × 1e6.
  assert.equal(hex(PacketBuilder.setCoordinates({ latitude: 30.2672, longitude: -97.7431 })), '0e40d7cd01048f2cfa00000000')
  assert.equal(PacketBuilder.scaledCoordinate(30.2672, PacketBuilder.latitudeRange), 30_267_200)
  assert.equal(PacketBuilder.scaledCoordinate(-97.7431, PacketBuilder.longitudeRange), -97_743_100)
})

test('setCoordinates clamps out of range and zeroes what is not a number', () => {
  assert.equal(hex(PacketBuilder.setCoordinates({ latitude: 200, longitude: -400 })), '0e804a5d05006b45f500000000')
  assert.equal(hex(PacketBuilder.setCoordinates({ latitude: NaN, longitude: Infinity })), '0e000000000000000000000000')
  assert.equal(PacketBuilder.setCoordinates({ latitude: 0, longitude: 0 }).length, 13)
})

test('setOtherParams packs the three telemetry modes into one byte', () => {
  // env in bits 5-4, loc in bits 3-2, base in bits 1-0: (1 << 4) | (2 << 2) | 3 = 0x1B.
  assert.equal(
    hex(PacketBuilder.setOtherParams({
      manualAddContacts: true,
      telemetryModeEnvironment: 1,
      telemetryModeLocation: 2,
      telemetryModeBase: 3,
      advertisementLocationPolicy: 2,
      multiAcks: 3,
    })),
    '26011b0203',
  )
})

test('setOtherParams omits the multi-ACK byte when it is not given', () => {
  assert.equal(
    hex(PacketBuilder.setOtherParams({ manualAddContacts: false, advertisementLocationPolicy: 1 })),
    '26000001',
  )
})

test('setName truncates to 31 UTF-8 bytes on a character boundary', () => {
  assert.equal(hex(PacketBuilder.setName('WX-AUS')), '0857582d415553')
  // Ten three-byte characters is 30 bytes; the eleventh would split across the 31-byte cut.
  const packet = PacketBuilder.setName('気'.repeat(20))
  assert.equal(packet.length, 1 + 30)
})
