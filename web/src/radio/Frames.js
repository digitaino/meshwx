// Frame codecs: the port of `MeshCore/Transport/WiFiFrameCodec.swift`.
//
// MeshCore has two framings and the transports differ only in which one they use:
//
//   * **Stream transports** (USB serial, and the WiFi/TCP bridge the Swift name comes from)
//     length-prefix every frame — `<` + u16 LE length + payload app to radio, `>` + u16 LE
//     length + payload radio to app. A stream splits and coalesces at will, so the decoder
//     is stateful and resynchronises on garbage by skipping to the next `>`.
//   * **BLE** does not frame at all: one notification on the Nordic UART RX characteristic
//     *is* one complete companion-protocol frame, and one write is one frame. The Swift
//     session says so where it parses (`handleReceivedData`: "each BLE notification is a
//     complete frame. No reassembly or buffering is needed"), and the BLE state machine
//     writes each command with a single `writeValue` — it never chunks. `BLEFrameCodec`
//     exists so both transports have the same shape, not because BLE needs a codec.

import { concatBytes } from './Bytes.js'

/** Length-prefixed framing for stream transports (serial, TCP). */
export const WiFiFrameCodec = Object.freeze({
  /** Outbound frame delimiter (app to device), `<`. */
  outboundDelimiter: 0x3c,
  /** Inbound frame delimiter (device to app), `>`. */
  inboundDelimiter: 0x3e,
  /** Header size: delimiter (1) + length (2). */
  headerSize: 3,

  /** `payload` framed for transmission: `<` + 2-byte little-endian length + payload. */
  encode(payload) {
    const length = payload.length & 0xffff
    return concatBytes(
      WiFiFrameCodec.outboundDelimiter,
      length & 0xff,
      (length >> 8) & 0xff,
      payload,
    )
  },
})

/**
 * Stateful decoder for incoming stream frames. Buffers partial data and extracts complete
 * frames; bytes before a `>` are dropped, which is how a link that came up mid-frame (or a
 * radio that printed a boot banner) resynchronises instead of jamming.
 */
export class WiFiFrameDecoder {
  #buffer = new Uint8Array(0)

  /** Every complete frame's payload in `data`, in order. Partial frames stay buffered. */
  decode(data) {
    this.#buffer = concatBytes(this.#buffer, data)
    const frames = []
    for (;;) {
      const frame = this.#extractFrame()
      if (frame == null) break
      frames.push(frame)
    }
    return frames
  }

  #extractFrame() {
    // Skip any bytes until the delimiter.
    let start = 0
    while (start < this.#buffer.length && this.#buffer[start] !== WiFiFrameCodec.inboundDelimiter) {
      start += 1
    }
    if (start > 0) this.#buffer = this.#buffer.slice(start)

    if (this.#buffer.length < WiFiFrameCodec.headerSize) return null

    const length = this.#buffer[1] | (this.#buffer[2] << 8)
    const totalFrameSize = WiFiFrameCodec.headerSize + length
    if (this.#buffer.length < totalFrameSize) return null

    const payload = this.#buffer.slice(WiFiFrameCodec.headerSize, totalFrameSize)
    this.#buffer = this.#buffer.slice(totalFrameSize)
    return payload
  }

  /** Clears the internal buffer. */
  reset() {
    this.#buffer = new Uint8Array(0)
  }
}

/**
 * BLE framing, which is no framing: a notification is a whole frame and a write is a whole
 * frame. Present so `WebBluetoothTransport` and `WebSerialTransport` read the same way.
 */
export const BLEFrameCodec = Object.freeze({
  /** A command payload goes out as-is, in one ATT write. */
  encode(payload) {
    return payload
  },
  /** One notification is one frame. */
  decode(notification) {
    return notification.length > 0 ? [notification] : []
  },
})
