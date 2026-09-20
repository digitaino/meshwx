// Wire bytes to events: the port of `MeshCore/Protocol/PacketParser.swift` and the
// `Parsers+*.swift` leaves this client needs.
//
// A `MeshEvent` is an object with `kind` set to the Swift case name and the associated
// values under their Swift labels (docs/PORTING.md §3); an unlabelled associated value is
// `value`. Dates are milliseconds.
//
// The parser never throws. A malformed packet is `{ kind: 'parseFailure' }` and a response
// code this client does not model is `{ kind: 'unknown' }` — a radio running newer firmware
// must not be able to kill the receive loop with a push we have not heard of.

import {
  decodeCString,
  readInt16LE,
  readInt32LE,
  readUInt16LE,
  readUInt32LE,
  snrValue,
  trimControlCharacters,
  utf8Decode,
} from './Bytes.js'
import {
  ContactType,
  PacketSize,
  ResponseCode,
  StatsType,
  decodePathLen,
  responseCodeName,
} from './PacketCodes.js'

const PUBLIC_KEY_SIZE = 32

function parseFailure(data, reason) {
  return { kind: 'parseFailure', data, reason }
}

/** Seconds on the wire to milliseconds since the epoch. */
function dateFromSeconds(seconds) {
  return seconds * 1000
}

export const Parsers = {
  /** Local device configuration (`RESP_CODE_SELF_INFO`, 57+ bytes). */
  SelfInfo: {
    parse(data) {
      if (data.length < PacketSize.selfInfoMinimum) {
        return parseFailure(
          data,
          `SelfInfo response too short: ${data.length} < ${PacketSize.selfInfoMinimum}`,
        )
      }
      let offset = 0
      const advertisementType = data[offset]
      offset += 1
      const txPower = (data[offset] << 24) >> 24
      offset += 1
      const maxTxPower = (data[offset] << 24) >> 24
      offset += 1
      const publicKey = data.slice(offset, offset + 32)
      offset += 32
      const latitude = readInt32LE(data, offset) / 1_000_000
      offset += 4
      const longitude = readInt32LE(data, offset) / 1_000_000
      offset += 4
      const multiAcks = data[offset]
      offset += 1
      const advertisementLocationPolicy = data[offset]
      offset += 1
      const telemetryMode = data[offset]
      offset += 1
      const manualAddContacts = data[offset] > 0
      offset += 1
      const radioFrequency = readUInt32LE(data, offset) / 1000
      offset += 4
      const radioBandwidth = readUInt32LE(data, offset) / 1000
      offset += 4
      const radioSpreadingFactor = data[offset]
      offset += 1
      const radioCodingRate = data[offset]
      offset += 1
      const name = trimControlCharacters(utf8Decode(data.subarray(offset)))

      return {
        kind: 'selfInfo',
        value: {
          advertisementType,
          txPower,
          maxTxPower,
          publicKey,
          latitude,
          longitude,
          multiAcks,
          advertisementLocationPolicy,
          telemetryModeEnvironment: (telemetryMode >> 4) & 0b11,
          telemetryModeLocation: (telemetryMode >> 2) & 0b11,
          telemetryModeBase: telemetryMode & 0b11,
          manualAddContacts,
          radioFrequency,
          radioBandwidth,
          radioSpreadingFactor,
          radioCodingRate,
          name,
        },
      }
    },
  },

  /** Device capabilities and versioning (`RESP_CODE_DEVICE_INFO`). */
  DeviceInfo: {
    parse(data) {
      if (data.length < 1) return parseFailure(data, 'DeviceInfo response empty')

      const firmwareVersion = data[0]
      let offset = 1
      let maxContacts = 0
      let maxChannels = 0
      let blePin = 0
      let firmwareBuild = ''
      let model = ''
      let version = ''

      if (firmwareVersion >= 3 && data.length < PacketSize.deviceInfoV3Full) {
        return parseFailure(
          data,
          `DeviceInfo v${firmwareVersion} response too short: ${data.length} < ${PacketSize.deviceInfoV3Full}`,
        )
      }

      if (firmwareVersion >= 3 && data.length >= PacketSize.deviceInfoV3Full) {
        maxContacts = data[offset] * 2 // Stored as count/2 in firmware.
        offset += 1
        maxChannels = data[offset]
        offset += 1
        blePin = readUInt32LE(data, offset)
        offset += 4
        firmwareBuild = trimControlCharacters(utf8Decode(data.subarray(offset, offset + 12)))
        offset += 12
        model = trimControlCharacters(utf8Decode(data.subarray(offset, offset + 40)))
        offset += 40
        version = trimControlCharacters(utf8Decode(data.subarray(offset, offset + 20)))
        offset += 20
      }

      // v9+: client_repeat, v10+: path_hash_mode. Both tolerant of an older/shorter frame.
      let clientRepeat = false
      if (firmwareVersion >= 9 && offset >= PacketSize.deviceInfoV3Full && data.length > offset) {
        clientRepeat = data[offset] !== 0
        offset += 1
      }
      let pathHashMode = 0
      if (firmwareVersion >= 10 && offset >= PacketSize.deviceInfoV3Full && data.length > offset) {
        pathHashMode = data[offset]
      }

      return {
        kind: 'deviceInfo',
        value: {
          firmwareVersion,
          maxContacts,
          maxChannels,
          blePin,
          firmwareBuild,
          model,
          version,
          clientRepeat,
          pathHashMode,
        },
      }
    },
  },

  /** A 147-byte contact record. */
  Contact: {
    parse(data) {
      if (data.length >= PacketSize.contact) {
        const pathLen = data[34]
        if (pathLen !== 0xff && decodePathLen(pathLen) == null) {
          return parseFailure(
            data,
            `Contact response uses reserved path length encoding: 0x${pathLen.toString(16).padStart(2, '0').toUpperCase()}`,
          )
        }
      }
      const contact = parseContactData(data)
      if (contact == null) {
        return parseFailure(
          data,
          `Contact response too short: ${data.length} < ${PacketSize.contact}`,
        )
      }
      return { kind: 'contact', value: contact }
    },
  },

  /** An advertisement from a node already known (32-byte public key). */
  Advertisement: {
    parse(data) {
      if (data.length < PUBLIC_KEY_SIZE) {
        return parseFailure(data, `Advertisement too short: ${data.length} < ${PUBLIC_KEY_SIZE}`)
      }
      return { kind: 'advertisement', publicKey: data.slice(0, PUBLIC_KEY_SIZE) }
    },
  },

  /**
   * An advertisement from a node not in the contact list (manual-add mode). Unlike
   * `advertisement`, which carries only a key, this one carries the whole contact record.
   */
  NewAdvertisement: {
    parse(data) {
      const contact = parseContactData(data)
      if (contact != null) return { kind: 'newContact', value: contact }
      if (data.length >= PUBLIC_KEY_SIZE) {
        return parseFailure(
          data,
          `NewAdvertisement has public key but insufficient contact data: ${data.length} < ${PacketSize.contact}`,
        )
      }
      return parseFailure(data, `NewAdvertisement too short: ${data.length}`)
    },
  },

  /** A routing-path update (32-byte public key). */
  PathUpdate: {
    parse(data) {
      if (data.length < PUBLIC_KEY_SIZE) {
        return parseFailure(data, `PathUpdate too short: ${data.length} < ${PUBLIC_KEY_SIZE}`)
      }
      return { kind: 'pathUpdate', publicKey: data.slice(0, PUBLIC_KEY_SIZE) }
    },
  },

  /**
   * Channel configuration.
   *
   * The name is a null-terminated C string in a 32-byte buffer: firmware uses `strcpy`, so
   * bytes after the null are uninitialised garbage and only the bytes before it are decoded.
   */
  ChannelInfo: {
    parse(data) {
      if (data.length < PacketSize.channelInfoMinimum) {
        return parseFailure(
          data,
          `ChannelInfo too short: ${data.length} < ${PacketSize.channelInfoMinimum}`,
        )
      }
      return {
        kind: 'channelInfo',
        value: {
          index: data[0],
          name: decodeCString(data.subarray(1, 33)),
          secret: data.slice(33, 49),
        },
      }
    },
  },

  /**
   * An incoming direct message.
   *
   * v3: `[snr×4:1][rsv:2][sender prefix:6][path_len:1][txt_type:1][ts u32 LE:4][text]`.
   * v1 is the same without the leading SNR and reserved bytes.
   */
  ContactMessage: {
    parse(data, { version }) {
      const minSize =
        version === 'v3' ? PacketSize.contactMessageV3Minimum : PacketSize.contactMessageV1Minimum
      if (data.length < minSize) {
        return parseFailure(data, `ContactMessage response too short: ${data.length} < ${minSize}`)
      }

      let offset = 0
      let snr = null
      if (version === 'v3') {
        snr = snrValue(data[offset])
        offset += 1
        offset += 2 // reserved
      }

      const senderPublicKeyPrefix = data.slice(offset, offset + 6)
      offset += 6
      const pathLength = data[offset]
      offset += 1
      const textType = data[offset]
      offset += 1
      const senderTimestamp = dateFromSeconds(readUInt32LE(data, offset))
      offset += 4

      let signature = null
      if (textType === 2) {
        if (data.length < offset + 4) {
          return parseFailure(
            data,
            `ContactMessage signature truncated: ${data.length} < ${offset + 4}`,
          )
        }
        signature = data.slice(offset, offset + 4)
        offset += 4
      }

      return {
        kind: 'contactMessageReceived',
        value: {
          senderPublicKeyPrefix,
          pathLength,
          textType,
          senderTimestamp,
          signature,
          text: utf8Decode(data.subarray(offset)),
          snr,
        },
      }
    },
  },

  /**
   * An incoming channel (broadcast) message.
   *
   * v3: `[snr×4:1][rsv:2][channel:1][path_len:1][txt_type:1][ts u32 LE:4][text]`.
   */
  ChannelMessage: {
    parse(data, { version }) {
      const minSize =
        version === 'v3' ? PacketSize.channelMessageV3Minimum : PacketSize.channelMessageV1Minimum
      if (data.length < minSize) {
        return parseFailure(data, `ChannelMessage response too short: ${data.length} < ${minSize}`)
      }

      let offset = 0
      let snr = null
      if (version === 'v3') {
        snr = snrValue(data[offset])
        offset += 1
        offset += 2 // reserved
      }

      const channelIndex = data[offset]
      offset += 1
      const pathLength = data[offset]
      offset += 1
      const textType = data[offset]
      offset += 1
      const senderTimestamp = dateFromSeconds(readUInt32LE(data, offset))
      offset += 4

      return {
        kind: 'channelMessageReceived',
        value: {
          channelIndex,
          pathLength,
          textType,
          senderTimestamp,
          text: utf8Decode(data.subarray(offset)),
          snr,
        },
      }
    },
  },

  /**
   * An incoming binary datagram on a channel (firmware v11+). This is how a MeshWX message
   * reaches the app.
   *
   * `[snr×4:1][rsv:2][channel:1][path_len:1][data_type u16 LE:2][data_len:1][payload]`.
   * A declared length past the end of the frame is clamped to what is actually there:
   * firmware guarantees the framing, but a buggy peer could lie, and half a datagram is
   * better diagnosed by the decoder above than dropped here.
   */
  ChannelDatagram: {
    parse(data) {
      if (data.length < PacketSize.channelDatagramMinimum) {
        return parseFailure(
          data,
          `ChannelDatagram response too short: ${data.length} < ${PacketSize.channelDatagramMinimum}`,
        )
      }
      let offset = 0
      const snr = snrValue(data[offset])
      offset += 1
      offset += 2 // reserved
      const channelIndex = data[offset]
      offset += 1
      const pathLength = data[offset]
      offset += 1
      const dataType = readUInt16LE(data, offset)
      offset += 2
      const declared = data[offset]
      offset += 1

      const length = Math.min(declared, data.length - offset)
      return {
        kind: 'channelDataReceived',
        value: {
          channelIndex,
          pathLength,
          dataType,
          data: data.slice(offset, offset + length),
          snr,
        },
      }
    },
  },

  /** Core system statistics: battery, uptime, errors, queue length. */
  CoreStats: {
    parse(data) {
      if (data.length < PacketSize.coreStatsMinimum) {
        return parseFailure(
          data,
          `CoreStats too short: ${data.length} < ${PacketSize.coreStatsMinimum}`,
        )
      }
      return {
        kind: 'statsCore',
        value: {
          batteryMV: readUInt16LE(data, 0),
          uptimeSeconds: readUInt32LE(data, 2),
          errors: readUInt16LE(data, 6),
          queueLength: data[8],
        },
      }
    },
  },

  /** Radio statistics: noise floor, RSSI, SNR, airtime. */
  RadioStats: {
    parse(data) {
      if (data.length < PacketSize.radioStatsMinimum) {
        return parseFailure(
          data,
          `RadioStats too short: ${data.length} < ${PacketSize.radioStatsMinimum}`,
        )
      }
      return {
        kind: 'statsRadio',
        value: {
          noiseFloor: readInt16LE(data, 0),
          lastRSSI: (data[2] << 24) >> 24,
          lastSNR: snrValue(data[3]),
          txAirtimeSeconds: readUInt32LE(data, 4),
          rxAirtimeSeconds: readUInt32LE(data, 8),
        },
      }
    },
  },

  /** Packet counters. */
  PacketStats: {
    parse(data) {
      if (data.length < PacketSize.packetStatsMinimum) {
        return parseFailure(
          data,
          `PacketStats too short: ${data.length} < ${PacketSize.packetStatsMinimum}`,
        )
      }
      const receiveErrors =
        data.length >= PacketSize.packetStatsWithReceiveErrors ? readUInt32LE(data, 24) : 0
      return {
        kind: 'statsPackets',
        value: {
          received: readUInt32LE(data, 0),
          sent: readUInt32LE(data, 4),
          floodTx: readUInt32LE(data, 8),
          directTx: readUInt32LE(data, 12),
          floodRx: readUInt32LE(data, 16),
          directRx: readUInt32LE(data, 20),
          receiveErrors,
        },
      }
    },
  },
}

/**
 * A 147-byte contact structure, or `null` when the frame is short or its path-length byte
 * uses the reserved encoding.
 *
 * `[pubkey:32][type:1][flags:1][path_len:1][path:64][name:32][last advert u32][lat i32]
 *  [lon i32][last modified u32]`.
 */
export function parseContactData(data) {
  if (data.length < PacketSize.contact) return null

  let offset = 0
  const publicKey = data.slice(offset, offset + 32)
  offset += 32
  const typeRawValue = data[offset]
  offset += 1
  const flags = data[offset]
  offset += 1
  const outPathLength = data[offset]
  offset += 1
  if (outPathLength !== 0xff && decodePathLen(outPathLength) == null) return null
  const actualPathLen = outPathLength === 0xff ? 0 : (decodePathLen(outPathLength)?.byteLength ?? 0)
  const pathField = data.subarray(offset, offset + 64)
  const outPath = actualPathLen > 0 ? pathField.slice(0, actualPathLen) : new Uint8Array(0)
  offset += 64
  // Null-terminated C string in a fixed 32-byte field.
  const advertisedName = trimControlCharacters(decodeCString(data.subarray(offset, offset + 32)))
  offset += 32
  const lastAdvertisement = dateFromSeconds(readUInt32LE(data, offset))
  offset += 4
  const latitude = readInt32LE(data, offset) / 1_000_000
  offset += 4
  const longitude = readInt32LE(data, offset) / 1_000_000
  offset += 4
  const lastModified = dateFromSeconds(readUInt32LE(data, offset))

  const known = Object.values(ContactType).includes(typeRawValue)
  return {
    id: hexOf(publicKey),
    publicKey,
    // The raw byte is kept so a type newer firmware knows and this client does not survives
    // instead of being coerced away.
    type: known ? typeRawValue : ContactType.chat,
    typeRawValue,
    flags,
    outPathLength,
    outPath,
    advertisedName,
    lastAdvertisement,
    latitude,
    longitude,
    lastModified,
  }
}

function hexOf(bytes) {
  let out = ''
  for (const byte of bytes) out += byte.toString(16).padStart(2, '0')
  return out
}

/**
 * One frame's bytes as a `MeshEvent`.
 *
 * Byte 0 is the response code; the rest is the payload handed to the leaf parser for that
 * code's domain.
 */
export const PacketParser = Object.freeze({
  parse(data) {
    if (data.length === 0) return parseFailure(data, 'Empty packet')

    const code = data[0]
    const payload = data.slice(1)

    switch (code) {
      // Simple
      case ResponseCode.ok:
        return { kind: 'ok', value: payload.length >= 4 ? readUInt32LE(payload, 0) : null }
      case ResponseCode.error:
        return { kind: 'error', code: payload.length > 0 ? payload[0] : null }

      // Device
      case ResponseCode.selfInfo:
        return Parsers.SelfInfo.parse(payload)
      case ResponseCode.deviceInfo:
        return Parsers.DeviceInfo.parse(payload)
      case ResponseCode.battery:
        return parseBattery(payload)
      case ResponseCode.currentTime:
        if (payload.length < 4) {
          return parseFailure(payload, `CurrentTime response too short: ${payload.length} < 4`)
        }
        return { kind: 'currentTime', value: dateFromSeconds(readUInt32LE(payload, 0)) }
      case ResponseCode.disabled:
        return { kind: 'disabled', reason: 'private_key_export_disabled' }

      // Contacts
      case ResponseCode.contactStart:
        if (payload.length < PacketSize.contactsStartMinimum) {
          return parseFailure(
            payload,
            `ContactStart response too short: ${payload.length} < ${PacketSize.contactsStartMinimum}`,
          )
        }
        return { kind: 'contactsStart', count: readUInt32LE(payload, 0) }
      case ResponseCode.contact:
        return Parsers.Contact.parse(payload)
      case ResponseCode.contactEnd:
        // `contactEnd` may carry the last-modified timestamp; older firmware sends none, and
        // this parser is pure, so a missing one is `null` rather than the Swift's `Date()`.
        return {
          kind: 'contactsEnd',
          lastModified: payload.length >= 4 ? dateFromSeconds(readUInt32LE(payload, 0)) : null,
        }
      case ResponseCode.contactURI:
        return { kind: 'contactURI', value: `meshcore://${hexOf(payload)}` }

      // Messages
      case ResponseCode.messageSent:
        if (payload.length < PacketSize.messageSentMinimum) {
          return parseFailure(
            payload,
            `MessageSent response too short: ${payload.length} < ${PacketSize.messageSentMinimum}`,
          )
        }
        return {
          kind: 'messageSent',
          value: {
            route: payload[0],
            expectedAck: payload.slice(1, 5),
            suggestedTimeoutMs: readUInt32LE(payload, 5),
          },
        }
      case ResponseCode.noMoreMessages:
        return { kind: 'noMoreMessages' }
      case ResponseCode.contactMessageReceived:
        return Parsers.ContactMessage.parse(payload, { version: 'v1' })
      case ResponseCode.contactMessageReceivedV3:
        return Parsers.ContactMessage.parse(payload, { version: 'v3' })
      case ResponseCode.channelMessageReceived:
        return Parsers.ChannelMessage.parse(payload, { version: 'v1' })
      case ResponseCode.channelMessageReceivedV3:
        return Parsers.ChannelMessage.parse(payload, { version: 'v3' })
      case ResponseCode.channelDataReceived:
        return Parsers.ChannelDatagram.parse(payload)

      // Pushes
      case ResponseCode.ack:
        if (payload.length < PacketSize.ackMinimum) {
          return parseFailure(
            payload,
            `Ack response too short: ${payload.length} < ${PacketSize.ackMinimum}`,
          )
        }
        return {
          kind: 'acknowledgement',
          code: payload.slice(0, PacketSize.ackMinimum),
          tripTime:
            payload.length >= PacketSize.ackWithTripTime
              ? readUInt32LE(payload, PacketSize.ackMinimum)
              : null,
        }
      case ResponseCode.messagesWaiting:
        return { kind: 'messagesWaiting' }
      case ResponseCode.advertisement:
        return Parsers.Advertisement.parse(payload)
      case ResponseCode.newAdvertisement:
        return Parsers.NewAdvertisement.parse(payload)
      case ResponseCode.pathUpdate:
        return Parsers.PathUpdate.parse(payload)
      case ResponseCode.contactDeleted:
        if (payload.length < PacketSize.contactDeletedPublicKey) {
          return parseFailure(
            payload,
            `ContactDeleted too short: ${payload.length} < ${PacketSize.contactDeletedPublicKey}`,
          )
        }
        return {
          kind: 'contactDeleted',
          publicKey: payload.slice(0, PacketSize.contactDeletedPublicKey),
        }
      case ResponseCode.contactsFull:
        return { kind: 'contactsFull' }

      // Misc
      case ResponseCode.channelInfo:
        return Parsers.ChannelInfo.parse(payload)
      case ResponseCode.stats:
        return parseStats(payload)

      default:
        // A code this client does not model. Named when we know the name (a response we
        // simply did not port), raw otherwise (newer firmware).
        return { kind: 'unknown', code, name: responseCodeName(code), data: payload }
    }
  },
})

function parseBattery(payload) {
  if (payload.length < PacketSize.batteryMinimum) {
    return parseFailure(
      payload,
      `Battery response too short: ${payload.length} < ${PacketSize.batteryMinimum}`,
    )
  }
  if (payload.length > PacketSize.batteryMinimum && payload.length < PacketSize.batteryExtended) {
    return parseFailure(
      payload,
      `Battery response has partial extended payload: ${payload.length} < ${PacketSize.batteryExtended}`,
    )
  }
  const level = readUInt16LE(payload, 0)
  const extended = payload.length >= PacketSize.batteryExtended
  return {
    kind: 'battery',
    value: {
      level,
      usedStorageKB: extended ? readUInt32LE(payload, 2) : null,
      totalStorageKB: extended ? readUInt32LE(payload, 6) : null,
    },
  }
}

function parseStats(payload) {
  if (payload.length < 1) return parseFailure(payload, `Stats response too short: ${payload.length} < 1`)
  const statsType = payload[0]
  const statsPayload = payload.slice(1)
  switch (statsType) {
    case StatsType.core:
      return Parsers.CoreStats.parse(statsPayload)
    case StatsType.radio:
      return Parsers.RadioStats.parse(statsPayload)
    case StatsType.packets:
      return Parsers.PacketStats.parse(statsPayload)
    default:
      return parseFailure(payload, `Unknown stats type: ${statsType}`)
  }
}
