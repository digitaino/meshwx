import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { decode, encode, hexToBytes, bytesToHex } from '../src/meshwx/index.js'
import { shiftTimes } from '../src/link/ReplayWeatherTransport.js'

const recording = JSON.parse(readFileSync(new URL('../demo/datagrams.json', import.meta.url), 'utf8'))

test('every recorded datagram decodes and re-encodes to the same bytes', () => {
  const kinds = new Map()
  for (const item of recording.datagrams) {
    const message = decode(hexToBytes(item.hex))
    kinds.set(message.name, (kinds.get(message.name) ?? 0) + 1)
    assert.equal(bytesToHex(encode(message)), item.hex, `${message.name} seq ${message.seq}`)
  }
  assert.ok(kinds.get('area_sweep') > 0 && kinds.get('observations') > 0 && kinds.get('warning') > 0)
})

test('shifting moves absolute times and keeps the packet length', () => {
  for (const item of recording.datagrams) {
    const message = decode(hexToBytes(item.hex))
    const moved = decode(encode(shiftTimes(message, 90)))
    assert.equal(encode(shiftTimes(message, 90)).length, item.hex.length / 2)
    for (const key of ['expires_min', 'now_min', 'ts_min', 'built_min', 'issued_min']) {
      if (Number.isInteger(message[key])) assert.equal(moved[key], message[key] + 90, key)
    }
  }
})
