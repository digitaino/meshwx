// An in-memory companion radio: the port in spirit of `MeshCore/Transport/MockTransport.swift`,
// grown into something that answers commands rather than just recording them.
//
// It behaves like the firmware closely enough to drive the whole session — handshake,
// channel slots, contact iteration, the message queue and its pushes — so the client can be
// tested, and later run end to end, without hardware. Everything it is given to send is
// recorded and offered to a host through `onChannelData` / `onMessage`, so a bridge can
// forward a Request datagram somewhere real.
//
// It speaks the same interface as the two browser transports: `connect`, `disconnect`,
// `send(frame)`, `subscribe(fn)`, `onDisconnect`. Frames in and out are bare companion
// payloads; no length prefix, as over BLE.

import { concatBytes, u16LE, u32LE, utf8Encode, utf8PaddedOrTruncated } from './Bytes.js'
import { CommandCode, ResponseCode, StatsType } from './PacketCodes.js'

const DEFAULT_PUBLIC_KEY = Uint8Array.from(
  Array.from({ length: 32 }, (_, i) => (i + 1) & 0xff),
)

/** A contact row the fake radio reports; the fields `PacketBuilder`-shaped bytes need. */
export function makeFakeContact({
  publicKey,
  type = 0x01,
  flags = 0,
  outPathLength = 0xff,
  outPath = new Uint8Array(0),
  advertisedName = 'WX-AUS',
  lastAdvertisement = 0,
  latitude = 0,
  longitude = 0,
  lastModified = 0,
}) {
  return {
    publicKey,
    type,
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

export class FakeRadioTransport {
  #subscribers = new Set()
  #connected = false

  /** Frames the session sent, in order, as raw command payloads. */
  sentFrames = []
  /** Every `sendChannelData` this radio was given. */
  channelDataSends = []
  /** Every DM this radio was given. */
  messageSends = []
  /** Every channel text message this radio was given. */
  channelMessageSends = []

  #channelDataListeners = new Set()
  #messageListeners = new Set()

  #queue = []
  #ackCounter = 0

  onDisconnect = null

  /**
   * @param {object} [options]
   * @param {number} [options.firmwareVersion] the device-query version code; 11 and up means
   *   the radio delivers and sends channel datagrams, which is the default because that is
   *   the radio this client is for.
   * @param {number} [options.maxChannels] channel slots this radio has
   * @param {number} [options.maxContacts]
   * @param {Uint8Array} [options.publicKey] the radio's own key
   * @param {string} [options.name]
   * @param {Array} [options.contacts] rows `getContacts` reports
   * @param {Map<number, {name: string, secret: Uint8Array}>} [options.channels] preset slots
   * @param {number} [options.deviceTime] the radio's clock, milliseconds
   * @param {boolean} [options.answerCommands] when false, nothing is answered — for testing
   *   the command timeout
   */
  constructor({
    firmwareVersion = 11,
    maxChannels = 40,
    maxContacts = 100,
    publicKey = DEFAULT_PUBLIC_KEY,
    name = 'FakeRadio',
    model = 'Fake T1000',
    contacts = [],
    channels = new Map(),
    deviceTime = Date.now(),
    answerCommands = true,
  } = {}) {
    this.firmwareVersion = firmwareVersion
    this.maxChannels = maxChannels
    this.maxContacts = maxContacts
    this.publicKey = publicKey
    this.name = name
    this.model = model
    this.contacts = contacts
    this.channels = channels
    this.deviceTime = deviceTime
    this.answerCommands = answerCommands
  }

  // MARK: - Transport interface

  get isConnected() {
    return this.#connected
  }

  async connect() {
    this.#connected = true
  }

  async disconnect() {
    this.#connected = false
    this.onDisconnect?.()
  }

  subscribe(fn) {
    this.#subscribers.add(fn)
    return () => this.#subscribers.delete(fn)
  }

  async send(frame) {
    if (!this.#connected) throw new Error('FakeRadioTransport: not connected')
    this.sentFrames.push(frame)
    if (!this.answerCommands) return
    this.#handleCommand(frame)
  }

  // MARK: - Host hooks

  /** Called with `{ channelIndex, dataType, payload, pathLength, pathBytes }` per datagram sent. */
  onChannelData(fn) {
    this.#channelDataListeners.add(fn)
    return () => this.#channelDataListeners.delete(fn)
  }

  /** Called with `{ to, text, timestamp, attempt, expectedAck }` per DM sent. */
  onMessage(fn) {
    this.#messageListeners.add(fn)
    return () => this.#messageListeners.delete(fn)
  }

  // MARK: - Delivering traffic to the client

  /** Queues a binary datagram and raises the messages-waiting push. */
  deliverChannelData({ channelIndex, dataType, data, snr = 0, pathLength = 0xff }) {
    const snrByte = Math.round(snr * 4) & 0xff
    this.#queue.push(
      concatBytes(
        ResponseCode.channelDataReceived,
        snrByte,
        0x00,
        0x00,
        channelIndex & 0xff,
        pathLength & 0xff,
        u16LE(dataType),
        data.length & 0xff,
        data,
      ),
    )
    this.#raiseMessagesWaiting()
  }

  /** Queues a channel text message (v3 push) and raises messages-waiting. */
  deliverChannelText({ channelIndex, text, timestamp = Date.now(), snr = 0, pathLength = 0xff }) {
    const snrByte = Math.round(snr * 4) & 0xff
    this.#queue.push(
      concatBytes(
        ResponseCode.channelMessageReceivedV3,
        snrByte,
        0x00,
        0x00,
        channelIndex & 0xff,
        pathLength & 0xff,
        0x00,
        u32LE(Math.floor(timestamp / 1000)),
        utf8Encode(text),
      ),
    )
    this.#raiseMessagesWaiting()
  }

  /** Queues a direct message (v3 push) and raises messages-waiting. */
  deliverContactMessage({
    senderPublicKeyPrefix,
    text,
    timestamp = Date.now(),
    snr = 0,
    pathLength = 0xff,
    textType = 0x00,
  }) {
    const snrByte = Math.round(snr * 4) & 0xff
    this.#queue.push(
      concatBytes(
        ResponseCode.contactMessageReceivedV3,
        snrByte,
        0x00,
        0x00,
        senderPublicKeyPrefix.subarray(0, 6),
        pathLength & 0xff,
        textType & 0xff,
        u32LE(Math.floor(timestamp / 1000)),
        utf8Encode(text),
      ),
    )
    this.#raiseMessagesWaiting()
  }

  /** Raises the delivery-ACK push for a code the radio handed out earlier. */
  deliverAcknowledgement({ code, tripTime = null }) {
    const parts = [ResponseCode.ack, code.subarray(0, 4)]
    if (tripTime != null) parts.push(u32LE(tripTime))
    this.#push(concatBytes(...parts))
  }

  /** Raises an advert push from a node already known. */
  deliverAdvertisement({ publicKey }) {
    this.#push(concatBytes(ResponseCode.advertisement, publicKey.subarray(0, 32)))
  }

  /** Emits a frame verbatim, for tests that need a malformed or unmodelled push. */
  deliverRaw(frame) {
    this.#push(frame)
  }

  // MARK: - Firmware behaviour

  #raiseMessagesWaiting() {
    if (!this.#connected) return
    this.#push(Uint8Array.of(ResponseCode.messagesWaiting))
  }

  #push(frame) {
    // Asynchronous like a real notification, so a push can never arrive inside the `send`
    // call that provoked it.
    queueMicrotask(() => {
      if (!this.#connected) return
      for (const subscriber of [...this.#subscribers]) subscriber(frame)
    })
  }

  #handleCommand(frame) {
    const code = frame[0]
    switch (code) {
      case CommandCode.appStart:
        this.#push(this.#selfInfoFrame())
        break

      case CommandCode.deviceQuery:
        this.#push(this.#deviceInfoFrame())
        break

      case CommandCode.getTime:
        this.#push(
          concatBytes(ResponseCode.currentTime, u32LE(Math.floor(this.deviceTime / 1000))),
        )
        break

      case CommandCode.setTime:
        this.deviceTime = (frame[1] | (frame[2] << 8) | (frame[3] << 16) | (frame[4] << 24)) * 1000
        this.#ok()
        break

      case CommandCode.getBattery:
        this.#push(concatBytes(ResponseCode.battery, u16LE(4100), u32LE(120), u32LE(1024)))
        break

      case CommandCode.getStats:
        this.#handleGetStats(frame)
        break

      case CommandCode.getContacts:
        this.#handleGetContacts()
        break

      case CommandCode.getMessage:
        this.#handleGetMessage()
        break

      case CommandCode.getChannel:
        this.#handleGetChannel(frame)
        break

      case CommandCode.setChannel:
        this.#handleSetChannel(frame)
        break

      case CommandCode.sendChannelData:
        this.#handleSendChannelData(frame)
        break

      case CommandCode.sendChannelMessage:
        this.#handleSendChannelMessage(frame)
        break

      case CommandCode.sendMessage:
        this.#handleSendMessage(frame)
        break

      case CommandCode.resetPath:
        this.#ok()
        break

      default:
        // Firmware answers an unknown command byte with ERR_CODE_UNSUPPORTED_CMD.
        this.#push(Uint8Array.of(ResponseCode.error, 1))
        break
    }
  }

  #ok() {
    this.#push(Uint8Array.of(ResponseCode.ok))
  }

  #error(code) {
    this.#push(Uint8Array.of(ResponseCode.error, code))
  }

  #selfInfoFrame() {
    return concatBytes(
      ResponseCode.selfInfo,
      0x01, // adv type
      22, // tx power
      30, // max tx power
      this.publicKey.subarray(0, 32),
      u32LE(0), // lat
      u32LE(0), // lon
      0x00, // multi acks
      0x00, // adv location policy
      0x00, // telemetry mode
      0x00, // manual add contacts
      u32LE(906_875), // radio frequency × 1000
      u32LE(250_000), // bandwidth × 1000
      11, // spreading factor
      8, // coding rate
      utf8Encode(this.name),
    )
  }

  #deviceInfoFrame() {
    return concatBytes(
      ResponseCode.deviceInfo,
      this.firmwareVersion & 0xff,
      Math.floor(this.maxContacts / 2) & 0xff,
      this.maxChannels & 0xff,
      u32LE(123456), // BLE pin
      utf8PaddedOrTruncated('20260101', 12),
      utf8PaddedOrTruncated(this.model, 40),
      utf8PaddedOrTruncated('1.15.0', 20),
      0x00, // client repeat (v9+)
      0x00, // path hash mode (v10+)
    )
  }

  #handleGetStats(frame) {
    switch (frame[1]) {
      case StatsType.core:
        this.#push(
          concatBytes(
            ResponseCode.stats,
            StatsType.core,
            u16LE(4100),
            u32LE(3600),
            u16LE(0),
            0x00,
          ),
        )
        break
      default:
        this.#error(6)
        break
    }
  }

  #handleGetContacts() {
    this.#push(concatBytes(ResponseCode.contactStart, u32LE(this.contacts.length)))
    for (const contact of this.contacts) this.#push(this.#contactFrame(contact))
    this.#push(concatBytes(ResponseCode.contactEnd, u32LE(Math.floor(this.deviceTime / 1000))))
  }

  #contactFrame(contact) {
    const path = new Uint8Array(64)
    path.set(contact.outPath.subarray(0, 64))
    return concatBytes(
      ResponseCode.contact,
      contact.publicKey.subarray(0, 32),
      contact.type & 0xff,
      contact.flags & 0xff,
      contact.outPathLength & 0xff,
      path,
      utf8PaddedOrTruncated(contact.advertisedName, 32),
      u32LE(Math.floor(contact.lastAdvertisement / 1000)),
      u32LE(Math.round(contact.latitude * 1_000_000)),
      u32LE(Math.round(contact.longitude * 1_000_000)),
      u32LE(Math.floor(contact.lastModified / 1000)),
    )
  }

  #handleGetMessage() {
    if (this.#queue.length === 0) {
      this.#push(Uint8Array.of(ResponseCode.noMoreMessages))
      return
    }
    this.#push(this.#queue.shift())
  }

  #handleGetChannel(frame) {
    const index = frame[1]
    if (index >= this.maxChannels) {
      // Past its last slot a radio answers an error, which reads as "not here".
      this.#error(2)
      return
    }
    const slot = this.channels.get(index)
    this.#push(
      concatBytes(
        ResponseCode.channelInfo,
        index,
        utf8PaddedOrTruncated(slot?.name ?? '', 32),
        slot?.secret ?? new Uint8Array(16),
      ),
    )
  }

  #handleSetChannel(frame) {
    const index = frame[1]
    if (index >= this.maxChannels) {
      this.#error(2)
      return
    }
    const nameField = frame.subarray(2, 34)
    const nullIndex = nameField.indexOf(0)
    const name = new TextDecoder().decode(
      nullIndex < 0 ? nameField : nameField.subarray(0, nullIndex),
    )
    this.channels.set(index, { name, secret: frame.slice(34, 50) })
    this.#ok()
  }

  #handleSendChannelData(frame) {
    const channelIndex = frame[1]
    const pathLength = frame[2]
    let offset = 3
    let pathBytes = new Uint8Array(0)
    if (pathLength !== 0xff) {
      // The fake radio cannot know the path length without decoding it, and the client only
      // ever floods; a direct-path send records the rest of the frame as its path + payload.
      const decodedBytes = (Math.floor(pathLength / 64) + 1) * (pathLength & 63)
      pathBytes = frame.slice(offset, offset + decodedBytes)
      offset += decodedBytes
    }
    const dataType = frame[offset] | (frame[offset + 1] << 8)
    offset += 2
    const payload = frame.slice(offset)

    const record = { channelIndex, dataType, payload, pathLength, pathBytes }
    this.channelDataSends.push(record)
    for (const listener of [...this.#channelDataListeners]) listener(record)
    this.#ok()
  }

  #handleSendChannelMessage(frame) {
    const record = {
      channel: frame[2],
      timestamp: (frame[3] | (frame[4] << 8) | (frame[5] << 16) | (frame[6] << 24)) * 1000,
      text: new TextDecoder().decode(frame.subarray(7)),
    }
    this.channelMessageSends.push(record)
    this.#ok()
  }

  #handleSendMessage(frame) {
    this.#ackCounter += 1
    const expectedAck = Uint8Array.of(
      this.#ackCounter & 0xff,
      (this.#ackCounter >> 8) & 0xff,
      0xab,
      0xcd,
    )
    const record = {
      attempt: frame[2],
      timestamp: (frame[3] | (frame[4] << 8) | (frame[5] << 16) | (frame[6] << 24)) * 1000,
      to: frame.slice(7, 13),
      text: new TextDecoder().decode(frame.subarray(13)),
      expectedAck,
    }
    this.messageSends.push(record)
    for (const listener of [...this.#messageListeners]) listener(record)
    this.#push(
      concatBytes(
        ResponseCode.messageSent,
        0x01, // route: flood
        expectedAck,
        u32LE(8000), // suggested timeout, ms
      ),
    )
  }
}
