// Command bytes: the port of `MeshCore/Protocol/PacketBuilder.swift`, limited to the
// commands this client sends.
//
// Every command is `[command code][command-specific payload]`; multi-byte integers are
// little-endian and strings UTF-8. Dates are milliseconds (docs/PORTING.md §3) and become
// the firmware's u32 seconds here.

import {
  concatBytes,
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

export const PacketBuilder = Object.freeze({
  publicKeySize,
  floodPathSentinel,
  channelDataMaxPayloadBytes,
  epochSeconds32,

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
