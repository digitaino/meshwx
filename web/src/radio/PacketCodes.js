// The wire's numbers: the port of `MeshCore/Protocol/PacketCodes.swift`,
// `ErrorCode.swift`, `PacketSize.swift` and `PathEncoding.swift` (merged, they are one
// subject: what a byte on the wire means).
//
// Swift enums with a raw value keep the number and get a name table (docs/PORTING.md §3).
// Only the codes this client needs are listed; the command table is kept complete enough
// that an unknown push can be named in a log.

/** Command codes sent from the app to the radio. */
export const CommandCode = Object.freeze({
  appStart: 0x01,
  sendMessage: 0x02,
  sendChannelMessage: 0x03,
  getContacts: 0x04,
  getTime: 0x05,
  setTime: 0x06,
  sendAdvertisement: 0x07,
  setName: 0x08,
  updateContact: 0x09,
  getMessage: 0x0a,
  resetPath: 0x0d,
  removeContact: 0x0f,
  reboot: 0x13,
  getBattery: 0x14,
  deviceQuery: 0x16,
  getChannel: 0x1f,
  setChannel: 0x20,
  getStats: 0x38,
  /** Sends a binary datagram to a channel. Firmware v11+ (MeshCore v1.15.0+). */
  sendChannelData: 0x3e,
})

/** Response codes received from the radio. */
export const ResponseCode = Object.freeze({
  ok: 0x00,
  error: 0x01,
  contactStart: 0x02,
  contact: 0x03,
  contactEnd: 0x04,
  selfInfo: 0x05,
  messageSent: 0x06,
  contactMessageReceived: 0x07,
  channelMessageReceived: 0x08,
  currentTime: 0x09,
  noMoreMessages: 0x0a,
  contactURI: 0x0b,
  battery: 0x0c,
  deviceInfo: 0x0d,
  privateKey: 0x0e,
  disabled: 0x0f,
  contactMessageReceivedV3: 0x10,
  channelMessageReceivedV3: 0x11,
  channelInfo: 0x12,
  stats: 0x18,
  /** A binary datagram arrived on a channel. Firmware v11+ (MeshCore v1.15.0+). */
  channelDataReceived: 0x1b,

  // Push notifications (0x80+)
  advertisement: 0x80,
  pathUpdate: 0x81,
  ack: 0x82,
  messagesWaiting: 0x83,
  newAdvertisement: 0x8a,
  contactDeleted: 0x8f,
  contactsFull: 0x90,
})

/**
 * Device error sub-codes carried by a `PACKET_ERROR` frame, mirroring the firmware's
 * `ERR_CODE_*` constants. The raw byte is preserved on the event so a sub-code outside this
 * range survives.
 */
export const ErrorCode = Object.freeze({
  unsupportedCommand: 1,
  notFound: 2,
  tableFull: 3,
  badState: 4,
  fileIOError: 5,
  illegalArgument: 6,
})

/** Message encoding types. */
export const TextType = Object.freeze({
  plainText: 0x00,
  cliData: 0x01,
  signed: 0x02,
})

/** Statistics categories `getStats` can ask for. */
export const StatsType = Object.freeze({
  core: 0x00,
  radio: 0x01,
  packets: 0x02,
})

/** Contact type identifier (firmware contact record, offset 32). */
export const ContactType = Object.freeze({
  chat: 0x01,
  repeater: 0x02,
  room: 0x03,
})

/** Bitfield flags of the firmware contact record (offset 33). */
export const ContactFlags = Object.freeze({
  favorite: 0x01,
  telemetryBase: 0x02,
  telemetryLocation: 0x04,
  telemetryEnvironment: 0x08,
})

function nameTable(table) {
  const out = {}
  for (const [name, value] of Object.entries(table)) out[value] = name
  return Object.freeze(out)
}

/** Raw command byte → Swift case name, for logs. */
export const CommandCodeName = nameTable(CommandCode)
/** Raw response byte → Swift case name, for logs and the `unknown` event. */
export const ResponseCodeName = nameTable(ResponseCode)
/** Raw error byte → Swift case name. */
export const ErrorCodeName = nameTable(ErrorCode)

/** The name of a response byte, or `null` when the firmware sent one we do not model. */
export function responseCodeName(code) {
  return ResponseCodeName[code] ?? null
}

/** The typed name of an error sub-code, or `null` outside the known range. */
export function errorCodeName(code) {
  return code == null ? null : (ErrorCodeName[code] ?? null)
}

/** Named packet-size constants, so no length check is a magic number. */
export const PacketSize = Object.freeze({
  contact: 147,
  selfInfoMinimum: 57,
  messageSentMinimum: 9,
  contactMessageV1Minimum: 12,
  contactMessageV3Minimum: 15,
  channelMessageV1Minimum: 7,
  channelMessageV3Minimum: 10,
  batteryMinimum: 2,
  batteryExtended: 10,
  deviceInfoV3Full: 79,
  ackMinimum: 4,
  ackWithTripTime: 8,
  contactsStartMinimum: 4,
  coreStatsMinimum: 9,
  radioStatsMinimum: 12,
  packetStatsMinimum: 24,
  packetStatsWithReceiveErrors: 28,
  channelInfoMinimum: 49,
  contactDeletedPublicKey: 32,
  /** `[snr:1][rsv:2][channel:1][path_len:1][data_type:2][data_len:1]` = 8 bytes. */
  channelDatagramMinimum: 8,
})

/** Limits of the multibyte path-length encoding. */
export const PathEncoding = Object.freeze({
  /** Highest valid hash-size mode (0 = 1-byte, 1 = 2-byte, 2 = 3-byte; mode 3 is reserved). */
  maxPathHashMode: 2,
  /** Highest hop count representable in the encoded byte's lower 6 bits. */
  maxHopCount: 63,
  /** Firmware `MAX_PATH_SIZE`. */
  maxPathBytes: 64,

  /**
   * Bytes per hop for a hash-size mode. The widths are linear (1/2/3), clamped so the
   * reserved mode 3 cannot ask for an oversized hash. Reading `path_sz` as a power of two
   * (`1 << mode`) built 4-byte hops in 3-byte mode and broke traces in the field; do not
   * restore that reading here.
   */
  hashSize(mode) {
    return Math.min(PathEncoding.maxPathHashMode + 1, mode + 1)
  },

  /** The mode a stored hop width came from — the inverse of `hashSize`. */
  mode(hashSize) {
    return Math.min(PathEncoding.maxPathHashMode, Math.max(0, hashSize - 1))
  },
})

/**
 * Decodes a multibyte-encoded path length byte into `{ hashSize, hopCount, byteLength }`,
 * or `null` for the reserved mode 3.
 *
 * Bits 7-6 are the hash-size mode, bits 5-0 the hop count.
 */
export function decodePathLen(encoded) {
  const mode = encoded >> 6
  if (mode >= 3) return null
  const hashSize = mode + 1
  const hopCount = encoded & 63
  return { hashSize, hopCount, byteLength: hashSize * hopCount }
}

/** Encodes hash size and hop count into one path-length byte; the inverse of `decodePathLen`. */
export function encodePathLen({ hashSize, hopCount }) {
  if (hashSize < 1 || hashSize > 3) throw new RangeError('hashSize must be 1, 2, or 3')
  const mode = hashSize - 1
  const hops = Math.min(hopCount, PathEncoding.maxHopCount)
  return ((mode << 6) | hops) & 0xff
}

/**
 * Firmware version code at which the radio both delivers and sends channel datagrams:
 * v11 is MeshCore v1.15.0 (`DeviceDTO.supportsChannelDatagrams`). Older firmware drops a
 * `GRP_DATA` packet silently — no error, no event — which is why the weather layer checks
 * this before promising anything.
 */
export const CHANNEL_DATAGRAM_FIRMWARE_VERSION = 11
