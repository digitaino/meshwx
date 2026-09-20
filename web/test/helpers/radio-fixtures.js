// Reference bytes and small builders shared by the radio tests.
//
// The command vectors are the ones `MeshCore/Tests/MeshCoreTests/Fixtures/PythonReferenceBytes.swift`
// holds: bytes extracted from the Python `meshcore_py` library, which the Swift builder is
// checked against. Checking the JS builder against the same bytes keeps all three clients on
// one wire format.

import { concatBytes, u32LE, utf8Encode, utf8PaddedOrTruncated } from '../../src/radio/index.js'

export const PythonReferenceBytes = {
  // b"\x01\x03" + 6 spaces + "MCore"
  appStart: Uint8Array.of(0x01, 0x03, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x4d, 0x43, 0x6f, 0x72, 0x65),
  deviceQuery: Uint8Array.of(0x16, 0x03),
  getBattery: Uint8Array.of(0x14),
  getTime: Uint8Array.of(0x05),
  setTime_1704067200: Uint8Array.of(0x06, 0x80, 0x00, 0x92, 0x65),
  setName_TestNode: concatBytes(0x08, utf8Encode('TestNode')),
  getContacts: Uint8Array.of(0x04),
  getMessage: Uint8Array.of(0x0a),
  sendMessage_Hello: Uint8Array.of(
    0x02, 0x00, 0x00, 0x80, 0x00, 0x92, 0x65, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0x48, 0x65,
    0x6c, 0x6c, 0x6f,
  ),
  sendChannelMessage_0_Hi: Uint8Array.of(0x03, 0x00, 0x00, 0x80, 0x00, 0x92, 0x65, 0x48, 0x69),
  getChannel_0: Uint8Array.of(0x1f, 0x00),
  sendAdvertisement: Uint8Array.of(0x07),
  sendAdvertisement_flood: Uint8Array.of(0x07, 0x01),
  reboot: concatBytes(0x13, utf8Encode('reboot')),
}

/** `2024-01-01T00:00:00Z`, the timestamp the Python vectors use, in milliseconds. */
export const REFERENCE_TIMESTAMP_MS = 1_704_067_200 * 1000

/** A `setChannel` packet as Python builds it: cmd, index, 32-byte padded name, 16-byte secret. */
export function pythonSetChannel(index, name, secret) {
  return concatBytes(0x20, index, utf8PaddedOrTruncated(name, 32), secret)
}

/** A `selfInfo` response frame with `publicKey` and `name`, otherwise zeroed. */
export function selfInfoFrame({ publicKey, name = 'Radio' }) {
  return concatBytes(
    0x05,
    0x01,
    22,
    30,
    publicKey,
    u32LE(0),
    u32LE(0),
    0,
    0,
    0,
    0,
    u32LE(906_875),
    u32LE(250_000),
    11,
    8,
    utf8Encode(name),
  )
}

/** A `channelDataReceived` (0x1B) frame. */
export function channelDataFrame({ channelIndex, dataType, data, snrRaw = 0, pathLength = 0xff }) {
  return concatBytes(
    0x1b,
    snrRaw,
    0x00,
    0x00,
    channelIndex,
    pathLength,
    dataType & 0xff,
    (dataType >> 8) & 0xff,
    data.length,
    data,
  )
}

/** Waits for the microtask queue and timers to settle, for tests that drive the fake radio. */
export function flush(times = 4) {
  let promise = Promise.resolve()
  for (let i = 0; i < times; i += 1) promise = promise.then(() => new Promise((r) => setTimeout(r, 0)))
  return promise
}
