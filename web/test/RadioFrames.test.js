// Ported from `MeshCore/Tests/MeshCoreTests/Transport/WiFiFrameCodecTests.swift`, same cases
// and same numbers, plus the resynchronisation case a byte stream needs and BLE does not.

import test from 'node:test'
import assert from 'node:assert/strict'
import { BLEFrameCodec, WiFiFrameCodec, WiFiFrameDecoder } from '../src/radio/index.js'

test('encodes a frame with the correct delimiter and length', () => {
  const encoded = WiFiFrameCodec.encode(Uint8Array.of(0x01, 0x02, 0x03))

  // '<' (0x3C) + length (3, 0) little-endian + payload
  assert.equal(encoded.length, 6)
  assert.equal(encoded[0], 0x3c)
  assert.equal(encoded[1], 0x03)
  assert.equal(encoded[2], 0x00)
  assert.equal(encoded[3], 0x01)
  assert.equal(encoded[4], 0x02)
  assert.equal(encoded[5], 0x03)
})

test('encodes an empty frame', () => {
  const encoded = WiFiFrameCodec.encode(new Uint8Array(0))
  assert.equal(encoded.length, 3)
  assert.equal(encoded[0], 0x3c)
  assert.equal(encoded[1], 0x00)
  assert.equal(encoded[2], 0x00)
})

test('decodes a single complete frame', () => {
  const decoder = new WiFiFrameDecoder()
  const frames = decoder.decode(Uint8Array.of(0x3e, 0x03, 0x00, 0x01, 0x02, 0x03))

  assert.equal(frames.length, 1)
  assert.deepEqual(frames[0], Uint8Array.of(0x01, 0x02, 0x03))
})

test('decodes multiple frames in one chunk', () => {
  const decoder = new WiFiFrameDecoder()
  const frames = decoder.decode(
    Uint8Array.of(0x3e, 0x02, 0x00, 0xaa, 0xbb, 0x3e, 0x01, 0x00, 0xcc),
  )

  assert.equal(frames.length, 2)
  assert.deepEqual(frames[0], Uint8Array.of(0xaa, 0xbb))
  assert.deepEqual(frames[1], Uint8Array.of(0xcc))
})

test('buffers an incomplete frame', () => {
  const decoder = new WiFiFrameDecoder()

  assert.equal(decoder.decode(Uint8Array.of(0x3e, 0x05, 0x00)).length, 0)
  assert.equal(decoder.decode(Uint8Array.of(0x01, 0x02)).length, 0)

  const frames = decoder.decode(Uint8Array.of(0x03, 0x04, 0x05))
  assert.equal(frames.length, 1)
  assert.deepEqual(frames[0], Uint8Array.of(0x01, 0x02, 0x03, 0x04, 0x05))
})

test('handles a frame split across chunks', () => {
  const decoder = new WiFiFrameDecoder()

  assert.equal(decoder.decode(Uint8Array.of(0x3e)).length, 0)
  assert.equal(decoder.decode(Uint8Array.of(0x03, 0x00, 0xaa)).length, 0)

  const frames = decoder.decode(Uint8Array.of(0xbb, 0xcc))
  assert.equal(frames.length, 1)
  assert.deepEqual(frames[0], Uint8Array.of(0xaa, 0xbb, 0xcc))
})

test('resynchronises past garbage before a delimiter', () => {
  const decoder = new WiFiFrameDecoder()
  // A boot banner, then a real frame.
  const frames = decoder.decode(
    Uint8Array.of(0x4d, 0x65, 0x73, 0x68, 0x0a, 0x3e, 0x02, 0x00, 0xaa, 0xbb),
  )

  assert.equal(frames.length, 1)
  assert.deepEqual(frames[0], Uint8Array.of(0xaa, 0xbb))
})

test('reset drops a half-received frame', () => {
  const decoder = new WiFiFrameDecoder()
  decoder.decode(Uint8Array.of(0x3e, 0x05, 0x00, 0x01))
  decoder.reset()

  const frames = decoder.decode(Uint8Array.of(0x3e, 0x01, 0x00, 0x42))
  assert.equal(frames.length, 1)
  assert.deepEqual(frames[0], Uint8Array.of(0x42))
})

test('BLE framing is no framing: one notification is one frame', () => {
  const payload = Uint8Array.of(0x1b, 0x00, 0x00)
  assert.equal(BLEFrameCodec.encode(payload), payload)
  assert.deepEqual(BLEFrameCodec.decode(payload), [payload])
  assert.deepEqual(BLEFrameCodec.decode(new Uint8Array(0)), [])
})
