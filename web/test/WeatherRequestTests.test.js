// Port of MC1ServicesTests/Weather/WeatherRequestTests.swift (docs/PORTING.md).
//
// The request grammar is the one part of the protocol the app *sends*, so every line of the
// spec §8.2 table is pinned here byte for byte.

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { WeatherPartsKind, WeatherReplyKind, WeatherRequest } from '../src/weather/index.js'

const R = WeatherRequest

describe('WeatherRequest', () => {
  it("every request renders the spec's wire text", () => {
    const expected = [
      [R.digest, '>d'],
      [R.activeWarnings, '>w'],
      [R.warning({ identity: 'SV.W.EWX.42' }), '>w SV.W.EWX.42'],
      [R.warningsTouching({ ugc: 'TXC453' }), '>w TXC453'],
      [R.warningsTouching({ ugc: 'TXZ192' }), '>w TXZ192'],
      [R.warningText({ identity: 'SV.W.EWX.42' }), '>wt SV.W.EWX.42'],
      [R.observations, '>o'],
      [R.observation({ station: 'KAUS' }), '>o KAUS'],
      [R.homeForecast, '>f'],
      [R.forecast({ point: 102 }), '>f 102'],
      [R.forecastForPlace('round rock tx'), '>f round rock tx'],
      [R.forecastDiscussion({ office: 'EWX' }), '>afd EWX'],
      [R.spaceWeather, '>space'],
      [R.stormReports({ state: 'TX' }), '>storm TX'],
      [R.rainfall({ state: 'TX' }), '>rain TX'],
      [R.metar({ station: 'KAUS' }), '>metar KAUS'],
      [R.taf({ station: 'KAUS' }), '>taf KAUS'],
      [R.hazardousOutlook, '>hwo'],
      [R.coverage, '>cov'],
      [R.areaSweep({ includesAdvisories: false }), '>wmap'],
      [R.areaSweep({ includesAdvisories: true }), '>wmap all'],
      // Revision 10.
      [R.areaSweep({ includesAdvisories: false, states: ['TX', 'OK'] }), '>wmap OKTX'],
      [R.areaSweep({ includesAdvisories: true, states: ['tx', 'ok'] }), '>wmap all OKTX'],
      [R.parts({ group: 212, indexes: [1, 4, 6], of: WeatherPartsKind.areaSweep }), '>part 212 1,4,6'],
      [R.forecastAt({ latitude: 35.687, longitude: -105.938 }), '>f 35.687,-105.938']
    ]
    for (const [request, text] of expected) {
      assert.equal(R.wireText(request), text)
    }
  })

  // Spec §8.3 lists the letters a Not-available reply can carry: w, o, f, a, s, r, m, t, h, d.
  it('request letters are the first letter after the prefix', () => {
    assert.equal(R.requestLetter(R.digest), 'd')
    assert.equal(R.requestLetter(R.activeWarnings), 'w')
    assert.equal(R.requestLetter(R.warningText({ identity: 'SV.W.EWX.42' })), 'w')
    assert.equal(R.requestLetter(R.observation({ station: 'KAUS' })), 'o')
    assert.equal(R.requestLetter(R.forecastForPlace('austin tx')), 'f')
    assert.equal(R.requestLetter(R.forecastDiscussion({ office: 'EWX' })), 'a')
    assert.equal(R.requestLetter(R.spaceWeather), 's')
    assert.equal(R.requestLetter(R.stormReports({ state: 'TX' })), 's')
    assert.equal(R.requestLetter(R.rainfall({ state: 'TX' })), 'r')
    assert.equal(R.requestLetter(R.metar({ station: 'KAUS' })), 'm')
    assert.equal(R.requestLetter(R.taf({ station: 'KAUS' })), 't')
    assert.equal(R.requestLetter(R.hazardousOutlook), 'h')
    assert.equal(R.requestLetter(R.coverage), 'c')
    // The sweep rides on `w` like every other warning request, so a refusal for it comes back
    // under the same letter (spec §8.3).
    assert.equal(R.requestLetter(R.areaSweep({ includesAdvisories: false })), 'w')
    assert.equal(R.requestLetter(R.areaSweep({ includesAdvisories: true })), 'w')
  })

  // Spec §7A: a statement describes the bot that sent it, so another bot's — or another
  // bot's answer to somebody else — says nothing about this one's area.
  it('only the bot asked can answer for its own area', () => {
    assert.equal(R.acceptsAnswerFromAnyBot(R.coverage), false)
    assert.equal(R.acceptsAnswerFromAnyBot(R.observations), false)
    assert.equal(R.acceptsAnswerFromAnyBot(R.observation({ station: 'KAUS' })), true)
    // Spec §7C: a sweep is one bot's reading of the country, cut where its own feed runs out.
    assert.equal(R.acceptsAnswerFromAnyBot(R.areaSweep({ includesAdvisories: false })), false)
    assert.equal(R.acceptsAnswerFromAnyBot(R.areaSweep({ includesAdvisories: true })), false)
  })

  it('expected replies carry the station, point and subject the request named', () => {
    assert.deepStrictEqual(R.expectedReply(R.digest), WeatherReplyKind.digest)
    assert.deepStrictEqual(R.expectedReply(R.activeWarnings), WeatherReplyKind.warnings)
    // Only the bare `>w` ends with a digest; the other two are that warning, or warnings naming
    // that area.
    assert.deepStrictEqual(
      R.expectedReply(R.warning({ identity: 'SV.W.EWX.42' })),
      WeatherReplyKind.warning({ identity: 'SV.W.EWX.42' })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.warningsTouching({ ugc: 'TXZ192' })),
      WeatherReplyKind.warningsTouching({ ugc: 'TXZ192' })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.observations), WeatherReplyKind.observations({ station: null })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.observation({ station: 'KAUS' })),
      WeatherReplyKind.observations({ station: 'KAUS' })
    )
    assert.deepStrictEqual(R.expectedReply(R.homeForecast), WeatherReplyKind.forecast({ point: null }))
    assert.deepStrictEqual(
      R.expectedReply(R.forecast({ point: 102 })), WeatherReplyKind.forecast({ point: 102 })
    )
    // A place is resolved by the bot; the point that comes back may even be 0xFFFF.
    assert.deepStrictEqual(
      R.expectedReply(R.forecastForPlace('round rock tx')), WeatherReplyKind.forecast({ point: null })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.warningText({ identity: 'x' })), WeatherReplyKind.text({ subject: 0 })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.forecastDiscussion({ office: 'EWX' })), WeatherReplyKind.text({ subject: 1 })
    )
    assert.deepStrictEqual(R.expectedReply(R.spaceWeather), WeatherReplyKind.text({ subject: 2 }))
    assert.deepStrictEqual(
      R.expectedReply(R.stormReports({ state: 'TX' })), WeatherReplyKind.text({ subject: 3 })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.rainfall({ state: 'TX' })), WeatherReplyKind.text({ subject: 4 })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.metar({ station: 'KAUS' })), WeatherReplyKind.text({ subject: 5 })
    )
    assert.deepStrictEqual(
      R.expectedReply(R.taf({ station: 'KAUS' })), WeatherReplyKind.text({ subject: 5 })
    )
    assert.deepStrictEqual(R.expectedReply(R.hazardousOutlook), WeatherReplyKind.text({ subject: 6 }))
    assert.deepStrictEqual(R.expectedReply(R.coverage), WeatherReplyKind.coverage)
    // Both scopes expect the same answer: the sweep's own flag says which one arrived.
    assert.deepStrictEqual(
      R.expectedReply(R.areaSweep({ includesAdvisories: false })), WeatherReplyKind.areaSweep
    )
    assert.deepStrictEqual(
      R.expectedReply(R.areaSweep({ includesAdvisories: true })), WeatherReplyKind.areaSweep
    )
  })

  // Two scopes are two requests: one on the air must not settle the other's button, and the
  // request log has to be able to say which one was asked for.
  it('the two map scopes are distinct requests', () => {
    assert.equal(
      R.isEqual(R.areaSweep({ includesAdvisories: false }), R.areaSweep({ includesAdvisories: true })),
      false
    )
    assert.notDeepStrictEqual(
      R.areaSweep({ includesAdvisories: false }), R.areaSweep({ includesAdvisories: true })
    )
  })

  // MARK: - Revision 10

  /**
   * The compact upper-case sorted form is what makes two taps that mean the same thing one
   * request: the wire text, the key and the answer slot are all built from it, so a selection of
   * "tx, ok" and one of "OK TX" are recognised as the same question by every one of them.
   */
  it('a sweep selection is normalised once, for the wire and for the key alike', () => {
    const one = R.areaSweep({ includesAdvisories: false, states: ['tx', 'OK', 'tx'] })
    const other = R.areaSweep({ includesAdvisories: false, states: [' ok ', 'Tx'] })
    assert.deepStrictEqual(one.states, ['OK', 'TX'])
    assert.equal(R.wireText(one), R.wireText(other))
    assert.equal(R.isEqual(one, other), true)
    assert.equal(R.key(one), 'areaSweep:narrow:OKTX')

    // A different selection is a different request, whatever the level.
    assert.equal(R.isEqual(one, R.areaSweep({ includesAdvisories: false, states: ['TX'] })), false)
    assert.equal(R.isEqual(one, R.areaSweep({ includesAdvisories: false })), false)
  })

  /**
   * A request written into the log, into a text assembly or into a settled outcome before
   * revision 10 has no `states` at all, and an area sweep without one was the national sweep it
   * was when it was written.
   */
  it('an area sweep persisted before revision 10 reads as national', () => {
    const old = { kind: 'areaSweep', includesAdvisories: true }
    assert.deepStrictEqual(R.areaSweepStates(old), [])
    assert.equal(R.wireText(old), '>wmap all')
    assert.equal(R.key(old), 'areaSweep:all:')
    assert.equal(R.isEqual(old, R.areaSweep({ includesAdvisories: true })), true)
  })

  it('part indexes are ascending and without duplicates', () => {
    const request = R.parts({ group: 7, indexes: [6, 1, 4, 1], of: WeatherPartsKind.text({ subject: 1 }) })
    assert.deepStrictEqual(request.indexes, [1, 4, 6])
    assert.equal(R.wireText(request), '>part 7 1,4,6')
    assert.equal(R.requestLetter(request), 'p')
    assert.deepStrictEqual(R.expectedReply(request), WeatherReplyKind.parts({ group: 7 }))
    assert.equal(R.acceptsAnswerFromAnyBot(request), false, 'only the bot asked holds that cache')
  })

  /**
   * "The kind is for the log's wording only." The bot's parts cache is keyed by the group byte
   * alone, across sweeps and texts alike, so two `>part 212 1` are one request whatever this
   * phone calls them — which is what the identical wire text already says.
   */
  it('the parts kind is not part of the request identity', () => {
    const sweep = R.parts({ group: 212, indexes: [1], of: WeatherPartsKind.areaSweep })
    const text = R.parts({ group: 212, indexes: [1], of: WeatherPartsKind.text({ subject: 0 }) })
    assert.equal(R.wireText(sweep), R.wireText(text))
    assert.equal(R.isEqual(sweep, text), true)
  })

  it('a coordinate forecast is three decimals, and its own key', () => {
    const santaFe = R.forecastAt({ latitude: 35.687, longitude: -105.938 })
    assert.equal(R.wireText(santaFe), '>f 35.687,-105.938')
    assert.equal(R.requestLetter(santaFe), 'f')
    // Trailing zeros are kept: the text is a function of the number, not of how it was written.
    assert.equal(R.wireText(R.forecastAt({ latitude: 30, longitude: -97.5 })), '>f 30.000,-97.500')
    // Rounded to the wire's precision, so the same town twice is the same request.
    assert.equal(R.key(R.forecastAt({ latitude: 35.6871, longitude: -105.9384 })), R.key(santaFe))
    assert.deepStrictEqual(R.expectedReply(santaFe), WeatherReplyKind.forecast({ point: null }))
    assert.equal(R.acceptsAnswerFromAnyBot(santaFe), false)
  })
})
