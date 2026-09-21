// What a radio will accept, and whether what somebody typed is inside it.
//
// Web only. The iOS app spreads these limits across its SwiftUI steppers and pickers; a browser
// form has to answer the same questions in one place, before a byte goes out: **never send a
// value outside the firmware's ranges**, because `CMD_SET_RADIO_PARAMS` answers
// `ERR_CODE_ILLEGAL_ARGUMENT` and leaves the radio on whatever it was on, which looks from the
// screen exactly like a write that worked.
//
// Pure: no DOM, no strings. A problem is `{ field, kind }` and the screen turns it into a
// sentence, so the same rules can be tested without a browser or a string table.

import {
  bandwidthRangeHz,
  codingRateRange,
  frequencyRangeKHz,
  latitudeRange,
  longitudeRange,
  radioScale,
  scaledRadioValue,
  spreadingFactorRange,
  txPowerFloor,
} from './PacketBuilder.js'

/** A `{ lowerBound, upperBound }` range divided by the firmware's ×1,000 scale. */
function unscaled(range) {
  return Object.freeze({
    lowerBound: range.lowerBound / radioScale,
    upperBound: range.upperBound / radioScale,
  })
}

const integersFrom = (range) => {
  const out = []
  for (let value = range.lowerBound; value <= range.upperBound; value += 1) out.push(value)
  return Object.freeze(out)
}

export const RadioParameters = Object.freeze({
  /** The frequency range in the unit a person types, MHz: 150 to 2500. */
  frequencyRangeMHz: unscaled(frequencyRangeKHz),
  /** The bandwidth range in the unit a person types, kHz: 7 to 500. */
  bandwidthRangeKHz: unscaled(bandwidthRangeHz),
  spreadingFactorRange,
  codingRateRange,
  latitudeRange,
  longitudeRange,
  txPowerFloor,

  /**
   * The standard LoRa bandwidths in kHz. Not every value in the firmware's 7 to 500 kHz range is
   * one a LoRa radio can be set to, and a mesh is a list of exact numbers everybody shares, so
   * these are offered as a list rather than as a free number.
   */
  bandwidths: Object.freeze([7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500]),
  spreadingFactors: integersFrom(spreadingFactorRange),
  codingRates: integersFrom(codingRateRange),

  /** Firmware holds the name in a `char[32]`, so 31 UTF-8 bytes and a null terminator. */
  nameMaxBytes: 31,

  /** A number from a text field, or `null` when the field does not hold one. */
  parseDecimal(text) {
    if (text == null) return null
    const trimmed = String(text).trim()
    if (trimmed === '') return null
    // `Number('')` is 0 and `Number('1,2')` is NaN: only a plain decimal counts.
    if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(trimmed)) return null
    const value = Number(trimmed)
    return Number.isFinite(value) ? value : null
  },

  /** UTF-8 bytes `name` would occupy in the firmware's field. */
  nameByteLength(name) {
    return new TextEncoder().encode(name ?? '').length
  },

  /**
   * The four numbers a radio needs to hear another radio, checked one by one.
   *
   * @returns {{ ok: boolean, problems: Array<{ field: string, kind: string }> }}
   */
  validateRadio({ frequency, bandwidth, spreadingFactor, codingRate }) {
    const problems = []
    const check = (field, value, range, { integer = false } = {}) => {
      if (value == null) { problems.push({ field, kind: 'missing' }); return }
      if (!Number.isFinite(value)) { problems.push({ field, kind: 'missing' }); return }
      if (integer && !Number.isInteger(value)) { problems.push({ field, kind: 'notWhole' }); return }
      if (value < range.lowerBound || value > range.upperBound) problems.push({ field, kind: 'outOfRange' })
    }
    check('frequency', frequency, RadioParameters.frequencyRangeMHz)
    check('bandwidth', bandwidth, RadioParameters.bandwidthRangeKHz)
    check('spreadingFactor', spreadingFactor, spreadingFactorRange, { integer: true })
    check('codingRate', codingRate, codingRateRange, { integer: true })
    return { ok: problems.length === 0, problems }
  },

  /**
   * Transmit power in dBm. The floor is the firmware's; the ceiling is this radio's own
   * `maxTxPower`, so a radio that has not said what it can do gets nothing sent to it.
   */
  validateTxPower(power, { maxTxPower }) {
    const problems = []
    if (!Number.isFinite(maxTxPower) || maxTxPower < txPowerFloor) {
      problems.push({ field: 'maxTxPower', kind: 'unknown' })
    }
    if (power == null || !Number.isFinite(power)) problems.push({ field: 'txPower', kind: 'missing' })
    else if (!Number.isInteger(power)) problems.push({ field: 'txPower', kind: 'notWhole' })
    else if (power < txPowerFloor || (Number.isFinite(maxTxPower) && power > maxTxPower)) {
      problems.push({ field: 'txPower', kind: 'outOfRange' })
    }
    return { ok: problems.length === 0, problems }
  },

  validatePosition({ latitude, longitude }) {
    const problems = []
    const check = (field, value, range) => {
      if (value == null || !Number.isFinite(value)) problems.push({ field, kind: 'missing' })
      else if (value < range.lowerBound || value > range.upperBound) problems.push({ field, kind: 'outOfRange' })
    }
    check('latitude', latitude, latitudeRange)
    check('longitude', longitude, longitudeRange)
    return { ok: problems.length === 0, problems }
  },

  /**
   * Whether two sets of radio parameters are the same *radio* — compared by the integers the
   * firmware persists, so 62.5 kHz typed and 62.5 kHz read back are one value and not two floats
   * that happen to differ in the last bit.
   */
  sameRadioParameters(a, b) {
    if (a == null || b == null) return false
    return (
      scaledRadioValue(a.frequency, frequencyRangeKHz) === scaledRadioValue(b.frequency, frequencyRangeKHz) &&
      scaledRadioValue(a.bandwidth, bandwidthRangeHz) === scaledRadioValue(b.bandwidth, bandwidthRangeHz) &&
      Number(a.spreadingFactor) === Number(b.spreadingFactor) &&
      Number(a.codingRate) === Number(b.codingRate)
    )
  },
})
