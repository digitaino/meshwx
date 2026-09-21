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
  assert.equal(kinds.get('radar'), 3, 'the three Austin tiles, one per width')
})

test('shifting moves absolute times and keeps the packet length', () => {
  for (const item of recording.datagrams) {
    const message = decode(hexToBytes(item.hex))
    const moved = decode(encode(shiftTimes(message, 90)))
    assert.equal(encode(shiftTimes(message, 90)).length, item.hex.length / 2)
    for (const key of ['expires_min', 'now_min', 'ts_min', 'built_min', 'issued_min', 'taken_min']) {
      if (Number.isInteger(message[key])) assert.equal(moved[key], message[key] + 90, key)
    }
  }
})

/**
 * The radar tiles have to read as minutes old, not hours. A picture's age is measured from the
 * minute printed on it, so a tile whose `taken` sat hours from the rest of the recording would
 * come out of the replay stale however new the packet around it was.
 */
test('the replayed radar pictures are a few minutes old, whenever they are replayed', () => {
  const items = recording.datagrams.filter((one) => !one.resend)
  const newest = Math.max(...items.map((one) => one.ts))
  const tiles = items
    .map((one) => decode(hexToBytes(one.hex)))
    .filter((one) => one.name === 'radar')
  assert.equal(tiles.length, 3)
  for (const at of [Date.now(), Date.parse('2027-03-01T00:00:00Z')]) {
    const shift = Math.floor((at / 1000 - newest) / 60)
    for (const tile of tiles) {
      const age = Math.floor(at / 60_000) - (tile.taken_min + shift)
      assert.ok(age >= 5 && age <= 20, `${age} min old at ${new Date(at).toISOString()}`)
    }
  }
})
