// Port of MC1ServicesTests/Weather/WeatherBotTests.swift (docs/PORTING.md).

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { WeatherBot } from '../src/weather/index.js'

/** A `ContactDTO` as the radio layer hands one over: the Swift property names, duck-typed. */
function contact({
  name, keyPrefix = [0x7a, 0x4c], latitude = 0, longitude = 0, lastAdvert = 0
} = {}) {
  const publicKey = Uint8Array.from([...keyPrefix, ...new Array(32 - keyPrefix.length).fill(0x11)])
  return { publicKey, name, lastAdvertTimestamp: lastAdvert, latitude, longitude }
}

describe('WeatherBot', () => {
  // The kit's vectors carry `bot = 19578` (0x4C7A) for a key starting `7A 4C`: little-endian.
  it('the bot id is the first two key bytes little-endian', () => {
    const bot = WeatherBot.fromContact(contact({ name: 'WX-AUS' }))
    assert.ok(bot != null)
    assert.equal(WeatherBot.botID(bot), 19578)
    assert.equal(WeatherBot.botIDForKey(Uint8Array.from([0x01])), 0)
    assert.equal(WeatherBot.city(bot), 'AUS')
  })

  it('only WX- names with a city are bots', () => {
    assert.notEqual(WeatherBot.fromContact(contact({ name: 'WX-AUS' })), null)
    assert.equal(WeatherBot.fromContact(contact({ name: 'WX-' })), null)
    assert.equal(WeatherBot.fromContact(contact({ name: 'wx-aus' })), null)
    assert.equal(WeatherBot.fromContact(contact({ name: 'Rafael' })), null)
    assert.equal(WeatherBot.fromContact(contact({ name: 'MyWX-Node' })), null)
  })

  it('an advert at 0,0 or never heard is reported as unknown', () => {
    const unplaced = WeatherBot.fromContact(contact({ name: 'WX-NOWHERE' }))
    assert.ok(unplaced != null)
    assert.equal(WeatherBot.hasLocation(unplaced), false)
    assert.equal(unplaced.lastAdvert, null)
    assert.equal(WeatherBot.distance(unplaced, { fromLatitude: 30, longitude: -97 }), null)

    const placed = WeatherBot.fromContact(contact({
      name: 'WX-AUS', latitude: 30.27, longitude: -97.74, lastAdvert: 1_700_000_000
    }))
    assert.ok(placed != null)
    assert.equal(WeatherBot.hasLocation(placed), true)
    assert.equal(placed.lastAdvert, 1_700_000_000 * 1000)
    const metres = WeatherBot.distance(placed, { fromLatitude: 30.27, longitude: -97.74 })
    assert.ok(metres != null && metres < 1)
  })

  it('bots sort nearest first, then the unplaced ones by name', () => {
    const contacts = [
      contact({ name: 'Rafael' }),
      contact({ name: 'WX-SAT', keyPrefix: [0x01, 0x02], latitude: 29.42, longitude: -98.49 }),
      contact({ name: 'WX-ZED', keyPrefix: [0x03, 0x04] }),
      contact({ name: 'WX-AUS', keyPrefix: [0x05, 0x06], latitude: 30.27, longitude: -97.74 }),
      contact({ name: 'WX-ALPHA', keyPrefix: [0x07, 0x08] })
    ]
    const fromAustin = WeatherBot.bots({ from: contacts, near: { latitude: 30.30, longitude: -97.70 } })
    assert.deepStrictEqual(fromAustin.map((bot) => bot.name), ['WX-AUS', 'WX-SAT', 'WX-ALPHA', 'WX-ZED'])

    const fromSanAntonio = WeatherBot.bots({
      from: contacts, near: { latitude: 29.40, longitude: -98.50 }
    })
    assert.deepStrictEqual(
      fromSanAntonio.map((bot) => bot.name), ['WX-SAT', 'WX-AUS', 'WX-ALPHA', 'WX-ZED']
    )

    const unlocated = WeatherBot.bots({ from: contacts, near: null })
    assert.deepStrictEqual(
      unlocated.map((bot) => bot.name), ['WX-ALPHA', 'WX-AUS', 'WX-SAT', 'WX-ZED']
    )
  })
})
