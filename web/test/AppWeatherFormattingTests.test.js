// Port of MC1Tests/Views/Tools/Weather/WeatherFormattingTests.swift (docs/PORTING.md).
//
// Units, durations and names as the Weather screen writes them (docs/MESHWX_UI.md). Same cases,
// same numbers; the Swift's `Calendar` and `Locale` are the IANA id and the BCP 47 tag.

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { MeshWXCompass, MeshWXSky, MeshWXWindReading } from '../src/meshwx/index.js'
import { WeatherNames } from '../src/screen/index.js'
import { WeatherFormatting, WeatherReferenceNames } from '../src/app/index.js'
import * as F from './helpers/app-fixture.js'

const tables = await F.loadTables()

const clock = (offsetSeconds) =>
  F.plain(
    WeatherFormatting.clockTime(F.now + offsetSeconds * 1000, {
      now: F.now,
      timeZone: F.timeZone,
      locale: F.locale,
    }),
  )

describe('Weather formatting', () => {
  // MARK: - Durations

  it('durations use the largest unit that fits', () => {
    assert.equal(WeatherFormatting.duration({ seconds: 40 }), '40 s')
    assert.equal(WeatherFormatting.duration({ seconds: 150 }), '2 min')
    assert.equal(WeatherFormatting.duration({ seconds: 3 * 3600 + 1200 }), '3 h')
    assert.equal(WeatherFormatting.duration({ seconds: 3 * 86_400 }), '3 d')
  })

  it('ages read as ago and old, with just now for a few seconds', () => {
    assert.equal(WeatherFormatting.ago(F.now - 2000, { now: F.now }), 'just now')
    assert.equal(WeatherFormatting.ago(F.now - 40_000, { now: F.now }), '40 s ago')
    assert.equal(WeatherFormatting.ago(F.now - 120_000, { now: F.now }), '2 min ago')
    assert.equal(WeatherFormatting.age(F.now - 3 * 3600 * 1000, { now: F.now }), '3 h old')
  })

  it('countdowns switch to hours at sixty minutes', () => {
    assert.equal(WeatherFormatting.countdown({ minutes: 40 }), 'in 40 min')
    assert.equal(WeatherFormatting.countdown({ minutes: 80 }), 'in 1 h 20 min')
    assert.equal(WeatherFormatting.countdown({ minutes: 120 }), 'in 2 h')
  })

  it('a quiet feed is minutes under an hour, then whole hours', () => {
    assert.equal(WeatherFormatting.quietDuration({ minutes: 45 }), '45 min')
    assert.equal(WeatherFormatting.quietDuration({ minutes: 300 }), '5 h')
  })

  it('times today and soon are clock times', () => {
    assert.equal(clock(-18 * 60), '11:02 PM')
    // 12:40 AM tomorrow: a warning ending after midnight still reads as a time.
    assert.equal(clock(80 * 60), '12:40 AM')
  })

  it('a past time on another day never passes for today', () => {
    assert.equal(clock(-26 * 3600), 'yesterday 9:20 PM')
    const older = clock(-3 * 86_400)
    assert.ok(older.startsWith('Sep 11') && older.endsWith('11:20 PM') && !older.includes('2026'), older)
    const later = clock(20 * 3600)
    assert.ok(later.startsWith('Sep 15') && later.endsWith('7:20 PM'), later)
  })

  it('the until line names the end and counts down to it', () => {
    const text = WeatherFormatting.untilLine({
      expiresAt: F.now + 40 * 60 * 1000,
      now: F.now,
      timeZone: F.timeZone,
      locale: F.locale,
    })
    assert.equal(F.plain(text), 'until 12:00 AM · in 40 min')
  })

  // MARK: - Distance

  it('distances are whole kilometres with a compass point', () => {
    assert.equal(WeatherFormatting.kilometres(3.2), '3 km')
    assert.equal(WeatherFormatting.kilometres(0.3), 'under 1 km')
    assert.equal(WeatherFormatting.distance(25.4, { direction: MeshWXCompass.north }), '25 km N')
    assert.equal(WeatherFormatting.distance(40, { direction: MeshWXCompass.northWest }), '40 km NW')
  })

  it('directions point from the place towards the target', () => {
    const austin = { latitude: 30.27, longitude: -97.74 }
    assert.equal(
      WeatherFormatting.direction({ from: austin, to: { latitude: 30.77, longitude: -97.74 } }),
      MeshWXCompass.north,
    )
    assert.equal(
      WeatherFormatting.direction({ from: austin, to: { latitude: 30.27, longitude: -98.74 } }),
      MeshWXCompass.west,
    )
  })

  // MARK: - Names

  it('a radio heard without an advert is named by its hex id', () => {
    assert.equal(WeatherFormatting.botName({ botID: 0x041d, bot: null }), 'Weather radio 041D')
  })

  it('a name starting a sentence is capitalised', () => {
    assert.equal(WeatherFormatting.sentenceStart('the weather radio'), 'The weather radio')
    assert.equal(WeatherFormatting.sentenceStart('WX-AUS'), 'WX-AUS')
  })

  // **One label per place** (docs/MESHWX_UI.md §3.1 U-12). The label a place carries is the name
  // it is shown by everywhere — the title bar, Places, every sentence that names it — and the
  // state stays on, because it is what tells two Austins apart.
  it('a place is named the same way everywhere', () => {
    assert.equal(WeatherFormatting.placeName('Round Rock, TX'), 'Round Rock, TX')
    assert.equal(WeatherFormatting.placeName('this location'), 'this location')
    assert.equal(WeatherFormatting.placeName(' Austin, TX '), 'Austin, TX')
  })

  it('firmware versions drop the v and a trailing zero', () => {
    assert.equal(WeatherFormatting.firmwareVersion('v1.14.0'), '1.14')
    assert.equal(WeatherFormatting.firmwareVersion('1.14.2'), '1.14.2')
    assert.equal(WeatherFormatting.firmwareVersion('dev'), 'dev')
  })

  // A forecast point is named by `WeatherNames.pointLabel`, the one function every site that
  // names one calls (docs/MESHWX_UI.md §3.1 U-25).
  it('forecast points read as places, by the one function that names them', () => {
    assert.equal(WeatherNames.pointState('Austin Camp Mabry-Travis TX'), 'TX')
    assert.equal(WeatherNames.pointState('Luis Munoz Marin International Airport-San Juan'), null)
    assert.equal(WeatherNames.pointLabel('Central Park-New York NY'), 'Central Park, NY')
    assert.equal(
      WeatherNames.pointLabel('Luis Munoz Marin International Airport-San Juan'),
      'Luis Munoz Marin International Airport',
    )
    assert.equal(WeatherNames.pointLabel('10 Mile Boxcars'), '10 Mile Boxcars')
  })

  it('an area named twice is listed once', () => {
    const runs = [
      { state: 42, county: true, start: 453, run: 1 },
      { state: 42, county: true, start: 209, run: 1 },
      { state: 42, county: true, start: 453, run: 1 },
    ]
    const areas = tables.namedAreas({ for: runs })
    assert.equal(areas.length, 3)
    assert.deepEqual(
      WeatherFormatting.uniqueAreas(areas).map((area) => area.ugc),
      ['TXC453', 'TXC209'],
    )
    assert.equal(WeatherFormatting.shortAreaName(areas[0]), 'Travis County')
  })

  it('offices are named by city and unknown ones by code', () => {
    assert.equal(WeatherReferenceNames.officeName('EWX'), 'NWS Austin/San Antonio')
    assert.equal(WeatherReferenceNames.officeName('FWD'), 'NWS Fort Worth')
    assert.equal(WeatherReferenceNames.officeName('ZZZ'), 'NWS ZZZ')
    assert.equal(WeatherReferenceNames.officeName('WNS'), 'Storm Prediction Center')
    assert.equal(WeatherReferenceNames.officeName('NHC'), 'National Hurricane Center')
    assert.equal(WeatherReferenceNames.stateName('TX'), 'Texas')
  })

  // Spec §6 (revision 3): whole miles rounded down, so 1/2SM arrives as 0.
  it('visibility under a mile reads under 1 mi, not 0', () => {
    assert.equal(WeatherFormatting.visibility({ miles: 0, locale: 'en-US' }), 'under 1 mi')
    assert.equal(WeatherFormatting.visibility({ miles: 10, locale: 'en-US' }), '10 mi')
  })

  // MARK: - Wind, pressure, tags

  it('a reported wind reads as direction, speed and gust', () => {
    const gusty = F.observation({ station: 0, windDegrees: 157.5, windMph: 12, gustMph: 21 })
    assert.equal(WeatherFormatting.wind(MeshWXWindReading.make({ observation: gusty })), 'SSE 12 gusting 21')
    const steady = F.observation({ station: 0, windDegrees: 180, windMph: 10, gustMph: 0 })
    assert.equal(WeatherFormatting.wind(MeshWXWindReading.make({ observation: steady })), 'S 10')
  })

  it('zero speed is calm and an unreported wind has no text', () => {
    const calm = F.observation({ station: 0, windDegrees: 0, windMph: 0, gustMph: 0 })
    assert.equal(WeatherFormatting.wind(MeshWXWindReading.make({ observation: calm })), 'calm')
    const missing = F.observation({ station: 0, windDegrees: 0, windMph: null })
    assert.equal(WeatherFormatting.wind(MeshWXWindReading.make({ observation: missing })), null)
  })

  it('pressure is two decimals of inches of mercury', () => {
    assert.equal(WeatherFormatting.pressure({ inchesOfMercury: 29.92, locale: F.locale }), '29.92')
  })

  it('tags read as the spec words them, on one line', () => {
    const subject = F.warning({
      event: 3,
      office: 1,
      etn: 42,
      expiresMinutes: 29_500_030,
      tornado: 2,
      floodDamage: 1,
      hailQuarterInches: 4,
      windMph: 60,
    })
    assert.deepEqual(WeatherFormatting.tagTexts({ for: subject, locale: F.locale }), [
      'Tornado: radar indicated',
      'Flash flood damage: considerable',
      'Hail 1.00 in',
      'Wind 60 mph',
    ])
    assert.equal(
      WeatherFormatting.tagLine({ for: subject, locale: F.locale }),
      'Tornado: radar indicated · Flash flood damage: considerable · Hail 1.00 in · Wind 60 mph',
    )
  })

  it('every sky code but other has a word', () => {
    for (let sky = 0; sky <= 15; sky += 1) {
      assert.equal(WeatherFormatting.condition(sky) == null, sky === MeshWXSky.other, `sky ${sky}`)
    }
  })

  it('the night icon runs from seven in the evening to six in the morning', () => {
    // 2026-09-14 in America/Chicago, which is UTC-5 that day.
    const at = (hour) => Date.UTC(2026, 8, 14, hour + 5, 0, 0)
    assert.equal(WeatherFormatting.isNight(at(23), { timeZone: F.timeZone }), true)
    assert.equal(WeatherFormatting.isNight(at(5), { timeZone: F.timeZone }), true)
    assert.equal(WeatherFormatting.isNight(at(12), { timeZone: F.timeZone }), false)
  })
})
