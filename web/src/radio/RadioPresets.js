// The community's radio presets: the port of `MC1Services/Services/RadioPresets.swift`, narrowed
// to the table and the lookup a web form needs (the locale-recommendation tiers, the repeat-mode
// snapping and the region-selection plumbing stay on the phone).
//
// It sits in `src/radio/` rather than a layer of its own because it is the radio's own vocabulary:
// four numbers with a name on them. Nothing above this layer should have to know the numbers.
//
// **A radio hears only radios on exactly the same four values.** That is why a preset is one
// object and not four defaults, and why `matchingPreset` is exact where the Swift's is
// approximate: the Swift allows 0.1 MHz and 1 kHz of slack, which would label a radio 75 kHz off
// "USA/Canada" and hide the one failure this table exists to make visible (a factory-fresh radio
// on the firmware's own frequency, deaf on a mesh it looks connected to).
//
// The last three rows are the Swift's `repeatPresets`, a separate list there because Repeat Mode
// sets only a frequency. They are valid four-value sets, so they are offered here like the rest.

import { bandwidthRangeHz, frequencyRangeKHz, scaledRadioValue } from './PacketBuilder.js'

/**
 * Regions, in the order the picker shows them. Not the Swift's `regionsForLocale`: a browser has
 * no reliable region and the order is the same for everybody, with North America first because
 * that is where this tool's mesh and its weather bot are.
 */
export const RadioRegion = Object.freeze({
  northAmerica: 'northAmerica',
  southAmerica: 'southAmerica',
  europe: 'europe',
  asia: 'asia',
  oceania: 'oceania',
})

/** The region order the picker groups by. */
export const radioRegionOrder = Object.freeze([
  RadioRegion.northAmerica,
  RadioRegion.southAmerica,
  RadioRegion.europe,
  RadioRegion.asia,
  RadioRegion.oceania,
])

const PRESETS = Object.freeze([
  { id: 'us-ca', name: 'USA/Canada', region: 'northAmerica', frequencyMHz: 910.525, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 5 },
  { id: 'wcmesh', name: 'WCMesh (SoCal)', region: 'northAmerica', frequencyMHz: 927.875, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 5 },
  { id: 'repeat-918', name: '918 MHz', region: 'northAmerica', frequencyMHz: 918, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 8 },

  { id: 'cl', name: 'Chile', region: 'southAmerica', frequencyMHz: 927.875, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 5 },
  { id: 'br', name: 'Brazil', region: 'southAmerica', frequencyMHz: 923.125, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 8 },

  { id: 'eu-narrow', name: 'EU/UK (Narrow)', region: 'europe', frequencyMHz: 869.618, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 8 },
  { id: 'eu-lr', name: 'EU/UK (Deprecated)', region: 'europe', frequencyMHz: 869.525, bandwidthKHz: 250, spreadingFactor: 11, codingRate: 5 },
  { id: 'cz-narrow', name: 'Czech Republic (Narrow)', region: 'europe', frequencyMHz: 869.432, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 5 },
  { id: 'eu-433-lr', name: 'EU 433MHz (Long Range)', region: 'europe', frequencyMHz: 433.65, bandwidthKHz: 250, spreadingFactor: 11, codingRate: 5 },
  { id: 'eu-433-narrow', name: 'EU 433MHz (Narrow)', region: 'europe', frequencyMHz: 433.65, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 8 },
  { id: 'pt-433', name: 'Portugal 433', region: 'europe', frequencyMHz: 433.375, bandwidthKHz: 62.5, spreadingFactor: 9, codingRate: 6 },
  { id: 'pt-868', name: 'Portugal 868', region: 'europe', frequencyMHz: 869.618, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 6 },
  { id: 'ch', name: 'Switzerland', region: 'europe', frequencyMHz: 869.618, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 8 },
  { id: 'nl', name: 'Netherlands', region: 'europe', frequencyMHz: 869.618, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 5 },
  { id: 'repeat-433', name: '433 MHz', region: 'europe', frequencyMHz: 433, bandwidthKHz: 62.5, spreadingFactor: 9, codingRate: 8 },
  { id: 'repeat-869', name: '869 MHz', region: 'europe', frequencyMHz: 869.495, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 8 },

  { id: 'vn-narrow', name: 'Vietnam (Narrow)', region: 'asia', frequencyMHz: 920.25, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 5 },
  { id: 'vn', name: 'Vietnam (Deprecated)', region: 'asia', frequencyMHz: 920.25, bandwidthKHz: 250, spreadingFactor: 11, codingRate: 5 },

  { id: 'au-915', name: 'Australia', region: 'oceania', frequencyMHz: 915.8, bandwidthKHz: 250, spreadingFactor: 10, codingRate: 5 },
  { id: 'au-narrow', name: 'Australia (Narrow)', region: 'oceania', frequencyMHz: 916.575, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 8 },
  { id: 'au-mid', name: 'Australia (Mid)', region: 'oceania', frequencyMHz: 915.075, bandwidthKHz: 125, spreadingFactor: 9, codingRate: 5 },
  { id: 'au-sa-wa', name: 'Australia: SA, WA', region: 'oceania', frequencyMHz: 923.125, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 8 },
  { id: 'au-qld', name: 'Australia: QLD', region: 'oceania', frequencyMHz: 923.125, bandwidthKHz: 62.5, spreadingFactor: 8, codingRate: 5 },
  { id: 'nz-lr', name: 'New Zealand', region: 'oceania', frequencyMHz: 917.375, bandwidthKHz: 250, spreadingFactor: 11, codingRate: 5 },
  { id: 'nz-narrow', name: 'New Zealand (Narrow)', region: 'oceania', frequencyMHz: 917.375, bandwidthKHz: 62.5, spreadingFactor: 7, codingRate: 5 },
].map((preset) => Object.freeze(preset)))

/** The kHz integer the firmware persists for a preset's frequency. */
function presetFrequencyKHz(preset) {
  return scaledRadioValue(preset.frequencyMHz, frequencyRangeKHz)
}

/** The Hz integer the firmware persists for a preset's bandwidth. */
function presetBandwidthHz(preset) {
  return scaledRadioValue(preset.bandwidthKHz, bandwidthRangeHz)
}

export const RadioPresets = Object.freeze({
  all: PRESETS,
  regions: radioRegionOrder,

  /** The preset with this id, or `null`. */
  byId(id) {
    return PRESETS.find((preset) => preset.id === id) ?? null
  },

  /**
   * The preset a radio is on, or `null` for anything else — which the picker calls Custom.
   *
   * Exact, on the integers the firmware persists (see the note at the top of this file).
   * `frequency` is MHz and `bandwidth` kHz, the units `selfInfo` reports.
   */
  matchingPreset({ frequency, bandwidth, spreadingFactor, codingRate }) {
    const frequencyKHz = scaledRadioValue(frequency, frequencyRangeKHz)
    const bandwidthHz = scaledRadioValue(bandwidth, bandwidthRangeHz)
    return (
      PRESETS.find(
        (preset) =>
          presetFrequencyKHz(preset) === frequencyKHz &&
          presetBandwidthHz(preset) === bandwidthHz &&
          preset.spreadingFactor === Number(spreadingFactor) &&
          preset.codingRate === Number(codingRate),
      ) ?? null
    )
  },

  /** `[{ region, presets }]` in `radioRegionOrder`, regions with no presets left out. */
  grouped() {
    return radioRegionOrder
      .map((region) => ({ region, presets: PRESETS.filter((preset) => preset.region === region) }))
      .filter((group) => group.presets.length > 0)
  },
})
