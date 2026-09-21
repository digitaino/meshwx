// Command bytes: the port of `MeshCore/Protocol/PacketBuilder.swift`, limited to the
// commands this client sends.
//
// Every command is `[command code][command-specific payload]`; multi-byte integers are
// little-endian and strings UTF-8. Dates are milliseconds (docs/PORTING.md §3) and become
// the firmware's u32 seconds here.

import {
  concatBytes,
  i32LE,
  u16LE,
  u32LE,
  utf8Encode,
  utf8Prefix,
  utf8PaddedOrTruncated,
} from './Bytes.js'
import { CommandCode, StatsType } from './PacketCodes.js'

/** Size of a public key in bytes. */
export const publicKeySize = 32
/** Flood sentinel for `path_len` fields: firmware treats `0xFF` as "route via flood". */
export const floodPathSentinel = 0xff
/** Maximum payload bytes for `CMD_SEND_CHANNEL_DATA` (`MAX_FRAME_SIZE - 9` per firmware). */
export const channelDataMaxPayloadBytes = 163
/** Fixed-point scale firmware applies to latitude/longitude (degrees × 1e6, stored as Int32). */
export const coordinateScale = 1_000_000
/** Fixed-point scale firmware applies to radio frequency and bandwidth (MHz/kHz × 1,000). */
export const radioScale = 1000

// The ranges `CMD_SET_RADIO_PARAMS` and `CMD_SET_TX_POWER` accept. A Swift `ClosedRange` is an
// object with its two bounds (docs/PORTING.md §3), so a caller can read them for a form's limits
// instead of restating the numbers.

/** Valid latitude range in degrees. */
export const latitudeRange = Object.freeze({ lowerBound: -90, upperBound: 90 })
/** Valid longitude range in degrees. */
export const longitudeRange = Object.freeze({ lowerBound: -180, upperBound: 180 })
/** Valid radio frequency range in kHz, matching firmware `CMD_SET_RADIO_PARAMS`. */
export const frequencyRangeKHz = Object.freeze({ lowerBound: 150_000, upperBound: 2_500_000 })
/** Valid radio bandwidth range in Hz, matching firmware `CMD_SET_RADIO_PARAMS`. */
export const bandwidthRangeHz = Object.freeze({ lowerBound: 7000, upperBound: 500_000 })
/** Valid LoRa spreading factor range, matching firmware `CMD_SET_RADIO_PARAMS`. */
export const spreadingFactorRange = Object.freeze({ lowerBound: 5, upperBound: 12 })
/** Valid LoRa coding rate range, matching firmware `CMD_SET_RADIO_PARAMS`. */
export const codingRateRange = Object.freeze({ lowerBound: 5, upperBound: 8 })
/**
 * Minimum LoRa transmit power in dBm (firmware rejects below this). The upper bound is the
 * device-reported `maxTxPower`, not a fixed constant, so it is supplied per device.
 */
export const txPowerFloor = -9

/**
 * Clamps a date to the firmware's unsigned 32-bit seconds-since-epoch field, saturating
 * pre-1970 and post-2106 dates instead of overflowing.
 *
 * @param {number} date milliseconds since the Unix epoch
 */
export function epochSeconds32(date) {
  const seconds = Math.floor(date / 1000)
  if (!Number.isFinite(seconds) || seconds <= 0) return 0
  if (seconds >= 0xffffffff) return 0xffffffff
  return seconds
}

/**
 * Scales a coordinate (degrees) into the firmware's `Int32` fixed-point form, clamping to a
 * finite, valid range. A NaN or infinite value becomes 0 rather than overflowing.
 *
 * Exported so a caller can compare two coordinates by the integer the device actually persists,
 * sidestepping float-equality noise between values that encode identically.
 *
 * `Math.trunc` is Swift's `Int32(_:)`: the conversion truncates toward zero, it does not round.
 */
export function scaledCoordinate(degrees, range) {
  const clamped = Number.isFinite(degrees)
    ? Math.min(Math.max(degrees, range.lowerBound), range.upperBound)
    : 0
  return Math.trunc(clamped * coordinateScale)
}

/**
 * Scales a radio value (MHz or kHz) by 1,000 into the firmware's `UInt32` field, rounding to the
 * nearest unit and clamping to `range`. Rounding avoids truncating a representable value one unit
 * low — 910.525 MHz is 910 525 kHz, not 910 524 — and clamping saturates a NaN, infinite or
 * out-of-range value instead of overflowing.
 *
 * Exported for the same reason as `scaledCoordinate`: preset matching compares the integers the
 * radio persists, not two floats.
 */
export function scaledRadioValue(value, range) {
  if (!Number.isFinite(value)) return range.lowerBound
  // Swift's `.rounded()` is to-nearest with ties away from zero; `Math.round` breaks ties toward
  // +∞, which differs only for a negative half, and these fields are unsigned.
  const scaled = value < 0 ? -Math.round(-value * radioScale) : Math.round(value * radioScale)
  return Math.min(Math.max(scaled, range.lowerBound), range.upperBound)
}

/**
 * A dBm value as the firmware's `Int8` field, two's complement in one byte.
 *
 * Swift's type system does the clamping (the parameter *is* an `Int8`); JS has to, and a value
 * outside the range saturates rather than wrapping into a wildly different power.
 */
function int8Byte(value) {
  const whole = Number.isFinite(value) ? Math.trunc(value) : 0
  return Math.min(Math.max(whole, -128), 127) & 0xff
}

export const PacketBuilder = Object.freeze({
  publicKeySize,
  floodPathSentinel,
  channelDataMaxPayloadBytes,
  coordinateScale,
  radioScale,
  latitudeRange,
  longitudeRange,
  frequencyRangeKHz,
  bandwidthRangeHz,
  spreadingFactorRange,
  codingRateRange,
  txPowerFloor,
  epochSeconds32,
  scaledCoordinate,
  scaledRadioValue,

  /**
   * appStart, which initialises the session.
   *
   * `[0x01][0x03][6 reserved spaces][clientId, 5 chars max]` — firmware reads the name from
   * byte 8.
   */
  appStart({ clientId = 'MCore' } = {}) {
    return concatBytes(
      CommandCode.appStart,
      0x03,
      [0x20, 0x20, 0x20, 0x20, 0x20, 0x20],
      utf8Encode(clientId.slice(0, 5)),
    )
  },

  /** deviceQuery: `[0x16][0x03]`. */
  deviceQuery() {
    return Uint8Array.of(CommandCode.deviceQuery, 0x03)
  },

  /** getBattery: `[0x14]`. */
  getBattery() {
    return Uint8Array.of(CommandCode.getBattery)
  },

  /** getTime: `[0x05]`. */
  getTime() {
    return Uint8Array.of(CommandCode.getTime)
  },

  /** setTime: `[0x06][unix seconds u32 LE]`. */
  setTime(date) {
    return concatBytes(CommandCode.setTime, u32LE(epochSeconds32(date)))
  },

  /** setName: `[0x08][name]`, 31 UTF-8 bytes max (firmware `char[32]`). */
  setName(name) {
    return concatBytes(CommandCode.setName, utf8Prefix(name, 31))
  },

  /**
   * setCoordinates: `[0x0E][latitude i32 LE][longitude i32 LE][0x00 × 4]`.
   *
   * Degrees × 1e6 as `Int32`; the last four bytes are the altitude field, which this client has
   * nothing to put in and the firmware reads as zero.
   */
  setCoordinates({ latitude, longitude }) {
    return concatBytes(
      CommandCode.setCoordinates,
      i32LE(scaledCoordinate(latitude, latitudeRange)),
      i32LE(scaledCoordinate(longitude, longitudeRange)),
      [0, 0, 0, 0],
    )
  },

  /** setTxPower: `[0x0C][power dBm Int8]`. */
  setTxPower(power) {
    return Uint8Array.of(CommandCode.setTxPower, int8Byte(power))
  },

  /**
   * setRadio: `[0x0B][frequency kHz u32 LE][bandwidth Hz u32 LE][spreading factor][coding rate]`,
   * plus one byte for the client-repeat flag (firmware v9+) when it is given.
   *
   * `frequency` is MHz and `bandwidth` kHz; both are scaled by 1,000 into the firmware's fields
   * and clamped there. The spreading factor and coding rate go out as given, exactly as the Swift
   * builder does: the firmware answers `ERR_CODE_ILLEGAL_ARGUMENT` for a value outside its range,
   * and a caller validates before sending (`RadioParameters.validateRadio`). Clamping them here
   * would quietly tune the radio to something nobody asked for.
   */
  setRadio({ frequency, bandwidth, spreadingFactor, codingRate, clientRepeat = null }) {
    const parts = [
      CommandCode.setRadio,
      u32LE(scaledRadioValue(frequency, frequencyRangeKHz)),
      u32LE(scaledRadioValue(bandwidth, bandwidthRangeHz)),
      spreadingFactor & 0xff,
      codingRate & 0xff,
    ]
    if (clientRepeat != null) parts.push(clientRepeat ? 1 : 0)
    return concatBytes(...parts)
  },

  /**
   * setOtherParams: `[0x26][manual add contacts 0/1][telemetry modes][advert location policy]`,
   * plus the multi-ACK byte (newer firmware) when it is given.
   *
   * The telemetry byte packs three two-bit modes — environment in bits 5-4, location in bits 3-2,
   * base in bits 1-0 — which is the layout `Parsers.SelfInfo` unpacks, so a caller can hand back
   * what self info reported and change one field.
   *
   * There is no partial form of this command: every field is written on every call, so a caller
   * that does not read the current values first overwrites them.
   */
  setOtherParams({
    manualAddContacts,
    telemetryModeEnvironment = 0,
    telemetryModeLocation = 0,
    telemetryModeBase = 0,
    advertisementLocationPolicy = 0,
    multiAcks = null,
  }) {
    const telemetryMode =
      ((telemetryModeEnvironment & 0b11) << 4) |
      ((telemetryModeLocation & 0b11) << 2) |
      (telemetryModeBase & 0b11)
    const parts = [
      CommandCode.setOtherParams,
      manualAddContacts ? 1 : 0,
      telemetryMode,
      advertisementLocationPolicy & 0xff,
    ]
    if (multiAcks != null) parts.push(multiAcks & 0xff)
    return concatBytes(...parts)
  },

  /** sendAdvertisement: `[0x07]`, or `[0x07][0x01]` to flood. */
  sendAdvertisement({ flood = false } = {}) {
    return flood
      ? Uint8Array.of(CommandCode.sendAdvertisement, 0x01)
      : Uint8Array.of(CommandCode.sendAdvertisement)
  },

  /** reboot: `[0x13]reboot`. */
  reboot() {
    return concatBytes(CommandCode.reboot, utf8Encode('reboot'))
  },

  /** getContacts: `[0x04]`, plus a u32 LE `since` timestamp for an incremental fetch. */
  getContacts({ since = null } = {}) {
    if (since == null) return Uint8Array.of(CommandCode.getContacts)
    return concatBytes(CommandCode.getContacts, u32LE(epochSeconds32(since)))
  },

  /** resetPath: `[0x0D][full 32-byte public key]`. */
  resetPath({ publicKey }) {
    return concatBytes(CommandCode.resetPath, publicKey.subarray(0, publicKeySize))
  },

  /** getMessage: `[0x0A]`, the sync-next-message command. */
  getMessage() {
    return Uint8Array.of(CommandCode.getMessage)
  },

  /**
   * sendMessage (a DM): `[0x02][type 0x00][attempt][ts u32 LE][dest prefix 6][text]`.
   *
   * The type byte is fixed to PLAIN. Firmware accepts only PLAIN or CLI_DATA for a DM;
   * CLI_DATA sets `expected_ack = 0`, which would defeat the ACK correlation this send path
   * depends on, so the type is not a parameter.
   */
  sendMessage({ to, text, timestamp, attempt = 0 }) {
    return concatBytes(
      CommandCode.sendMessage,
      0x00,
      attempt & 0xff,
      u32LE(epochSeconds32(timestamp)),
      to.subarray(0, 6),
      utf8Encode(text),
    )
  },

  /** sendChannelMessage: `[0x03][type 0x00][channel][ts u32 LE][text]`. */
  sendChannelMessage({ channel, text, timestamp }) {
    return concatBytes(
      CommandCode.sendChannelMessage,
      0x00,
      channel & 0xff,
      u32LE(epochSeconds32(timestamp)),
      utf8Encode(text),
    )
  },

  /**
   * sendChannelData, the binary datagram (firmware v11+).
   *
   * `[0x3E][channel][path_len]([path bytes])[data_type u16 LE][payload]`. Path bytes are
   * written verbatim and only when `pathLength !== 0xFF`: firmware ignores them for a flood,
   * so the builder omits them. The payload is clamped to 163 bytes.
   *
   * Upstream `companion_protocol.md` §6 documents an incorrect wire format (wrong field
   * order, missing `path_len`) as of v1.15.0. `MyMesh.cpp` is canonical.
   */
  sendChannelData({
    channelIndex,
    dataType,
    payload,
    pathLength = floodPathSentinel,
    pathBytes = new Uint8Array(0),
  }) {
    const parts = [CommandCode.sendChannelData, channelIndex & 0xff, pathLength & 0xff]
    if ((pathLength & 0xff) !== floodPathSentinel) parts.push(pathBytes)
    parts.push(u16LE(dataType))
    parts.push(payload.subarray(0, channelDataMaxPayloadBytes))
    return concatBytes(...parts)
  },

  /** getChannel: `[0x1F][index]`. */
  getChannel({ index }) {
    return Uint8Array.of(CommandCode.getChannel, index & 0xff)
  },

  /** setChannel: `[0x20][index][name, 32 zero-padded UTF-8 bytes][secret, 16 bytes]`. */
  setChannel({ index, name, secret }) {
    return concatBytes(
      CommandCode.setChannel,
      index & 0xff,
      utf8PaddedOrTruncated(name, 32),
      secret.subarray(0, 16),
    )
  },

  /** getStats for the core counters: `[0x38][0x00]`. */
  getStatsCore() {
    return Uint8Array.of(CommandCode.getStats, StatsType.core)
  },

  /** getStats for the radio counters: `[0x38][0x01]`. */
  getStatsRadio() {
    return Uint8Array.of(CommandCode.getStats, StatsType.radio)
  },

  /** getStats for the packet counters: `[0x38][0x02]`. */
  getStatsPackets() {
    return Uint8Array.of(CommandCode.getStats, StatsType.packets)
  },
})
