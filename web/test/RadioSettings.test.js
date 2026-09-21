// The pure rules behind the radio settings screen: the preset table and its lookup, what a radio
// will accept, and whether the radio has heard anything at all.
//
// Web only. `RadioPresets` is the narrowed port of `MC1Services/Services/RadioPresets.swift`;
// `RadioParameters` and `RadioActivity` have no Swift original — a phone's steppers enforce the
// limits and a phone's radio belongs to the whole app.

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  RadioActivity,
  RadioParameters,
  RadioPresets,
  radioRegionOrder,
} from '../src/radio/index.js'

// MARK: - Presets

test('the preset table is the app s, 25 rows with distinct ids', () => {
  assert.equal(RadioPresets.all.length, 25)
  assert.equal(new Set(RadioPresets.all.map((preset) => preset.id)).size, 25)
  for (const preset of RadioPresets.all) {
    assert.ok(radioRegionOrder.includes(preset.region), `${preset.id} has region ${preset.region}`)
    assert.equal(RadioParameters.validateRadio({
      frequency: preset.frequencyMHz,
      bandwidth: preset.bandwidthKHz,
      spreadingFactor: preset.spreadingFactor,
      codingRate: preset.codingRate,
    }).ok, true, `${preset.id} is inside the firmware ranges`)
  }
})

test('the owner s mesh is us-ca: 910.525 MHz, 62.5 kHz, SF 7, CR 5', () => {
  assert.deepEqual(RadioPresets.byId('us-ca'), {
    id: 'us-ca',
    name: 'USA/Canada',
    region: 'northAmerica',
    frequencyMHz: 910.525,
    bandwidthKHz: 62.5,
    spreadingFactor: 7,
    codingRate: 5,
  })
  assert.equal(RadioPresets.byId('nothing-like-this'), null)
})

test('a radio on a preset s four values is matched by id', () => {
  const matched = RadioPresets.matchingPreset({
    frequency: 910.525, bandwidth: 62.5, spreadingFactor: 7, codingRate: 5,
  })
  assert.equal(matched?.id, 'us-ca')
})

test('matching is exact: a radio a shade off a preset is Custom, not that preset', () => {
  // The Swift allows 0.1 MHz and 1 kHz of slack. Here it must not: 910.6 MHz cannot hear 910.525,
  // and a picker that called it "USA/Canada" would hide the failure the screen exists to show.
  assert.equal(RadioPresets.matchingPreset({ frequency: 910.6, bandwidth: 62.5, spreadingFactor: 7, codingRate: 5 }), null)
  assert.equal(RadioPresets.matchingPreset({ frequency: 910.525, bandwidth: 62.4, spreadingFactor: 7, codingRate: 5 }), null)
  assert.equal(RadioPresets.matchingPreset({ frequency: 910.525, bandwidth: 62.5, spreadingFactor: 8, codingRate: 5 }), null)
  assert.equal(RadioPresets.matchingPreset({ frequency: 910.525, bandwidth: 62.5, spreadingFactor: 7, codingRate: 6 }), null)
})

test('the firmware default a fresh radio ships on matches no preset', () => {
  // 906.875 MHz, 250 kHz, SF 11, CR 8: what the owner plugged in and found deaf.
  assert.equal(
    RadioPresets.matchingPreset({ frequency: 906.875, bandwidth: 250, spreadingFactor: 11, codingRate: 8 }),
    null,
  )
})

test('matching compares the integers the radio persists, not two floats', () => {
  // 62.5 × 1000 is exact, but 41.7 × 1000 is 41 699.999999999996 in binary floating point.
  const preset = RadioPresets.all.find((one) => one.bandwidthKHz === 250 && one.id === 'nz-lr')
  assert.equal(
    RadioPresets.matchingPreset({
      frequency: preset.frequencyMHz, bandwidth: preset.bandwidthKHz,
      spreadingFactor: preset.spreadingFactor, codingRate: preset.codingRate,
    })?.id,
    'nz-lr',
  )
  assert.equal(RadioParameters.sameRadioParameters(
    { frequency: 917.375, bandwidth: 41.7, spreadingFactor: 11, codingRate: 5 },
    { frequency: 917.375, bandwidth: 41.7, spreadingFactor: 11, codingRate: 5 },
  ), true)
})

test('presets are grouped in region order and every row appears once', () => {
  const groups = RadioPresets.grouped()
  assert.deepEqual(groups.map((group) => group.region), ['northAmerica', 'southAmerica', 'europe', 'asia', 'oceania'])
  assert.equal(groups.reduce((total, group) => total + group.presets.length, 0), RadioPresets.all.length)
  assert.equal(groups[0].presets[0].id, 'us-ca')
})

// MARK: - What a radio will accept

test('the ranges are the firmware s, in the units a person types', () => {
  assert.deepEqual(RadioParameters.frequencyRangeMHz, { lowerBound: 150, upperBound: 2500 })
  assert.deepEqual(RadioParameters.bandwidthRangeKHz, { lowerBound: 7, upperBound: 500 })
  assert.deepEqual(RadioParameters.spreadingFactors, [5, 6, 7, 8, 9, 10, 11, 12])
  assert.deepEqual(RadioParameters.codingRates, [5, 6, 7, 8])
  assert.equal(RadioParameters.txPowerFloor, -9)
  assert.deepEqual(RadioParameters.bandwidths, [7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500])
  // Every offered bandwidth is one the firmware accepts.
  for (const bandwidth of RadioParameters.bandwidths) {
    assert.equal(RadioParameters.validateRadio({ frequency: 910.525, bandwidth, spreadingFactor: 7, codingRate: 5 }).ok, true)
  }
})

test('validateRadio names the field and what is wrong with it', () => {
  assert.deepEqual(RadioParameters.validateRadio({ frequency: 910.525, bandwidth: 62.5, spreadingFactor: 7, codingRate: 5 }), {
    ok: true, problems: [],
  })
  assert.deepEqual(
    RadioParameters.validateRadio({ frequency: 100, bandwidth: 62.5, spreadingFactor: 7, codingRate: 5 }).problems,
    [{ field: 'frequency', kind: 'outOfRange' }],
  )
  assert.deepEqual(
    RadioParameters.validateRadio({ frequency: null, bandwidth: null, spreadingFactor: null, codingRate: null }).problems,
    [
      { field: 'frequency', kind: 'missing' },
      { field: 'bandwidth', kind: 'missing' },
      { field: 'spreadingFactor', kind: 'missing' },
      { field: 'codingRate', kind: 'missing' },
    ],
  )
  assert.deepEqual(
    RadioParameters.validateRadio({ frequency: 910.525, bandwidth: 62.5, spreadingFactor: 7.5, codingRate: 5 }).problems,
    [{ field: 'spreadingFactor', kind: 'notWhole' }],
  )
  assert.deepEqual(
    RadioParameters.validateRadio({ frequency: 910.525, bandwidth: 62.5, spreadingFactor: 13, codingRate: 4 }).problems,
    [{ field: 'spreadingFactor', kind: 'outOfRange' }, { field: 'codingRate', kind: 'outOfRange' }],
  )
})

test('transmit power runs from the firmware floor to the radio s own maximum', () => {
  assert.equal(RadioParameters.validateTxPower(22, { maxTxPower: 30 }).ok, true)
  assert.equal(RadioParameters.validateTxPower(-9, { maxTxPower: 30 }).ok, true)
  assert.deepEqual(RadioParameters.validateTxPower(-10, { maxTxPower: 30 }).problems, [{ field: 'txPower', kind: 'outOfRange' }])
  assert.deepEqual(RadioParameters.validateTxPower(31, { maxTxPower: 30 }).problems, [{ field: 'txPower', kind: 'outOfRange' }])
  // A radio that has not said what it can do gets nothing sent to it.
  assert.deepEqual(RadioParameters.validateTxPower(22, { maxTxPower: null }).problems, [{ field: 'maxTxPower', kind: 'unknown' }])
})

test('a position has to be on the earth', () => {
  assert.equal(RadioParameters.validatePosition({ latitude: 30.2672, longitude: -97.7431 }).ok, true)
  assert.equal(RadioParameters.validatePosition({ latitude: 0, longitude: 0 }).ok, true)
  assert.deepEqual(RadioParameters.validatePosition({ latitude: 91, longitude: 0 }).problems, [{ field: 'latitude', kind: 'outOfRange' }])
  assert.deepEqual(RadioParameters.validatePosition({ latitude: 0, longitude: -181 }).problems, [{ field: 'longitude', kind: 'outOfRange' }])
  assert.deepEqual(RadioParameters.validatePosition({ latitude: null, longitude: null }).problems, [
    { field: 'latitude', kind: 'missing' },
    { field: 'longitude', kind: 'missing' },
  ])
})

test('parseDecimal accepts a number and nothing else', () => {
  assert.equal(RadioParameters.parseDecimal('910.525'), 910.525)
  assert.equal(RadioParameters.parseDecimal('  62.5 '), 62.5)
  assert.equal(RadioParameters.parseDecimal('-97.7431'), -97.7431)
  assert.equal(RadioParameters.parseDecimal('.5'), 0.5)
  assert.equal(RadioParameters.parseDecimal('7'), 7)
  assert.equal(RadioParameters.parseDecimal(''), null)
  assert.equal(RadioParameters.parseDecimal('   '), null)
  assert.equal(RadioParameters.parseDecimal(null), null)
  assert.equal(RadioParameters.parseDecimal('910,525'), null)
  assert.equal(RadioParameters.parseDecimal('910.5 MHz'), null)
  assert.equal(RadioParameters.parseDecimal('1e3'), null)
  assert.equal(RadioParameters.parseDecimal('Infinity'), null)
})

test('a name is measured in UTF-8 bytes, not characters', () => {
  assert.equal(RadioParameters.nameMaxBytes, 31)
  assert.equal(RadioParameters.nameByteLength('WX-AUS'), 6)
  assert.equal(RadioParameters.nameByteLength('気象'), 6)
  assert.equal(RadioParameters.nameByteLength(null), 0)
})

// MARK: - Whether anything was heard

test('nothing heard is the cue that the four values are wrong', () => {
  // Only this page's own commands came back: the radio is answering and hearing nobody.
  const quiet = { connectionStateChanged: 3, ok: 4, selfInfo: 2, currentTime: 1, noMoreMessages: 1 }
  assert.deepEqual(RadioActivity.heard(quiet), { adverts: 0, messages: 0, total: 0 })
  assert.deepEqual(RadioActivity.heard({}), { adverts: 0, messages: 0, total: 0 })
  assert.deepEqual(RadioActivity.heard(undefined), { adverts: 0, messages: 0, total: 0 })
})

test('adverts and messages are counted apart, and a message is counted once', () => {
  const busy = {
    advertisement: 4, newContact: 1, pathUpdate: 2,
    channelDataReceived: 3, channelMessageReceived: 1, contactMessageReceived: 2,
    // The poller re-emits every message it pulled; counting these too would double each one.
    channelData: 3, channelMessage: 1, contactMessage: 2,
    ok: 9,
  }
  assert.deepEqual(RadioActivity.heard(busy), { adverts: 7, messages: 6, total: 13 })
})
